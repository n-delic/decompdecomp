"""Code an N64 game loads later: segments outside the main one that splat's create_config finds.

splat finds the boot code and the main code segment. Many games keep more code elsewhere in the ROM and load it at
run time to an address of its own: Super Mario 64's engine (0x80378800) and Mario's head on the title screen
("goddard", 0x8016F000) are a third of its code. Nothing in the ROM says where such code goes, but the code itself
does: its calls (jal) are absolute addresses, and they land on its own functions' starts. So:

1. find stretches of the ROM that look like MIPS code (functions returning with `jr $ra`, stack frames made with
   `addiu $sp, $sp, -N`);
2. for each, the load address that makes most of its calls land on stack-frame starts in it;
3. its first function: the lowest address anything calls in it (the main code calls into it too), its end: the
   last return.

Segments whose addresses overlap code already known (overlays sharing one address) are left out: function
addresses stay unique in a project.

Code can also be compressed in the ROM (unpack_code): Rare's games keep theirs as deflate streams after a 0x1172 or
0x1173 marker (Perfect Dark: 0x1173 and the unpacked size, then the stream), Nintendo's in Yaz0 (Ocarina of Time's,
Majora's Mask's). Such code is unpacked and appended to a working copy of the ROM, where the same search finds it.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

from ...arch.mips import JR_RA, SCAN_BLOCK, code_before, is_frame, jal_targets, place
from ...rom import RomError, split

BLOCK = SCAN_BLOCK
# where code can run: KSEG0 (RAM), or mapped through the TLB (GoldenEye's game code runs at 0x7F000000). A jal keeps
# the top four bits of the address it's at, so the region decides what its targets are.
REGIONS = ((0x80000000, 0x81000000), (0x70000000, 0x80000000))
KSEG0 = REGIONS[0][0]  # where the known code (the main segment) runs


def _code_like(words: tuple[int, ...]) -> bool:
    """A return or a stack frame: either is rare in anything but code (a large function can go a while without the
    other)."""
    return any(w == JR_RA or is_frame(w) for w in words)


def find_segments(rom: bytes, taken: list[tuple[int, int, int]], ends: tuple[int, ...] = (), *, log=print,
                  declared: tuple[tuple[int, int, int], ...] = ()) -> list[tuple[int, int, int]]:
    """(rom_start, rom_end, vram) of code segments outside `taken` (rom_start, rom_end, vram of the known ones).
    `ends`: where the known segments end, their data included (splat's config): code found after one can begin
    right there. `declared`: those segments whole (rom_start, rom_end, vram), where a switch's jump table is read."""
    size = len(rom) & ~3
    known_code = [(s, e) for s, e, _ in taken]
    calls: list[int] = []  # every call the known code makes outside itself: some go into the code found here
    known_vram = [(v, v + e - s) for s, e, v in taken]
    for s, e in known_code:
        calls += [t for t in jal_targets(struct.unpack(f">{(e - s) // 4}I", rom[s:e - (e - s) % 4]), KSEG0)
                  if not any(a <= t < b for a, b in known_vram)]
    gaps, pos = [], 0x1000
    for s, e in sorted(known_code):
        if s > pos:
            gaps.append((pos, s))
        pos = max(pos, e)
    if pos < size:
        gaps.append((pos, size))

    candidates = []  # (start, end, vram, how sure): every stretch placed first, the surest kept first
    stretches = []  # every stretch of code, for the second pass
    for g_lo, g_hi in gaps:
        regions, cur = [], None
        for off in range(g_lo, g_hi - 0x20, BLOCK):
            end = min(off + BLOCK, g_hi) & ~3
            if _code_like(struct.unpack(f">{(end - off) // 4}I", rom[off:end])):
                if cur and off <= cur[1] + 2 * BLOCK:  # a quiet stretch inside a segment is fine
                    cur[1] = end
                else:
                    cur = [off, end]
                    regions.append(cur)
        floor = g_lo
        for lo, hi in regions:
            floor = max([floor] + [e for e in ends if e < hi])  # (data has no code: one ending in it is before)
            stretches.append((lo, hi, floor))
            seg = place(rom, lo, hi, floor, calls, known_vram, REGIONS)
            if seg and seg[0] < seg[1]:
                candidates.append(seg)
                floor = seg[1]
    found = []
    used_vram = [(v, v + (e - s)) for s, e, v in taken]

    def accept(start: int, end: int, vram: int) -> bool:
        # The last return can precede the first plausible entry in a stretch.
        # An empty range claims no bytes and cannot become a splat segment.
        if (not 0 <= start < end <= size
                or any(a < vram + (end - start) and vram < b for a, b in used_vram)
                or any(a < end and start < b for a, b, _ in found)):
            return False
        found.append((start, end, vram))
        used_vram.append((vram, vram + end - start))
        return True

    for start, end, vram, _ in sorted(candidates, key=lambda c: -c[3]):  # a weak guess never blocks a sure one
        accept(start, end, vram)
    # Second pass: a stretch too small (or too quiet) to place by itself usually calls functions of a stretch placed
    # already, in the same segment: try the mappings found (address minus ROM offset), counting calls that land on
    # a function start anywhere in the image.
    mappings = collections.Counter(v - s for s, _, v in found)
    frame_at = {i * 4 for i, w in enumerate(struct.unpack(f">{size // 4}I", rom[:size])) if is_frame(w)}
    for lo, hi, floor in stretches:
        if any(a <= lo < b or a < hi <= b for a, b, _ in found):
            continue
        words = struct.unpack(f">{(hi - lo) // 4}I", rom[lo:hi])
        best = None
        for mapping in mappings:
            own = jal_targets(words, mapping + lo)
            hits = sum(1 for t in own if (t - mapping) in frame_at)
            if hits >= 3 and (best is None or hits > best[0]):
                best = (hits, mapping)
        if best is None:
            continue
        mapping = best[1]
        frames = [i * 4 for i, w in enumerate(words) if is_frame(w)]
        returns = [i * 4 for i, w in enumerate(words) if w == JR_RA]
        if not frames or not returns:
            continue
        start = code_before(rom, lo + frames[0], floor)
        end = min((lo + returns[-1] + 8 + 0xF) & ~0xF, len(rom))
        accept(start, end, start + mapping)
    from .code_tails import complete
    from .loader_code import complete as loaded_code
    from .loader_mapping import refine, verified

    found=complete(rom,sorted(found),taken)
    found=reconcile_between(rom,found,taken,log)
    found=reconcile_adjacent(rom,found,taken,log)
    from .code_references import complete as referenced_gaps

    found=refine(rom,found+referenced_gaps(rom,sorted(taken+found),declared),taken,log)
    return close_gaps(rom, loaded_code(rom,found,verified(rom,taken),taken,log), taken)


def reconcile_between(rom, found, taken=(), log=print):
    """Correct a middle range when both neighbours and independent calls agree."""
    ordered = sorted(found)
    result = list(ordered)
    callers = sorted((*taken, *ordered))
    for i in range(1, len(ordered)-1):
        lo, hi, old = ordered[i]
        left, right = ordered[i-1], ordered[i+1]
        mapping = left[2] - left[0]
        if (right[2] - right[0] != mapping or mapping == old-lo
                or not 0 <= lo-left[1] <= 16 or not 0 <= right[0]-hi <= 16
                or abs(mapping-(old-lo)) > 16):
            continue
        base = lo+mapping
        if (not 0x80000400 <= base < base+hi-lo <= 0x80800000
                or any(a < hi and lo < b or v < base+hi-lo and base < v+b-a
                       for a,b,v in callers if (a,b,v) != ordered[i])):
            continue
        words = struct.unpack_from(f'>{(hi-lo)//4}I', rom, lo)
        entries = {lo+4*j for j,w in enumerate(words)
                   if is_frame(w) or j >= 2 and words[j-2] == JR_RA}
        support = [set(), set()]
        old_support = [set(), set()]
        for start, end, entry in callers:
            targets = jal_targets(struct.unpack_from(f'>{(end-start)//4}I',rom,start),entry)
            inside = start == lo and end == hi
            for target in targets:
                if target-mapping in entries:
                    support[inside].add(target)
                if target-(old-lo) in entries:
                    old_support[inside].add(target)
        if (all(len(hits) >= 3 for hits in support)
                and all(len(good) > len(bad) for good,bad in zip(support,old_support))
                and sum(map(len,support)) >= 2*max(1,sum(map(len,old_support)))):
            result[i] = lo, hi, base
            log('Recovered a middle code mapping from neighbouring ranges and independent calls.')
    return result


def reconcile_adjacent(rom, found, taken=(), log=print):
    """Keep one mapping across adjacent inferred text when calls corroborate it."""
    ordered = sorted(found)
    if len(ordered) < 2:
        return ordered
    callers = sorted((*taken, *ordered))
    result = [ordered[0]]
    for lo, hi, vram in ordered[1:]:
        before, left_end, left_vram = result[-1]
        expected = left_vram+lo-before
        # Test the preceding mapping only when the two ranges already meet in
        # ROM and the inferred address jumps forward by a small amount.
        if lo != left_end or not 0 < vram-expected <= BLOCK:
            result.append((lo, hi, vram))
            continue
        if any(start < hi and lo < end or ram < expected+hi-lo and expected < ram+end-start
               for start, end, ram in callers if (start, end, ram) != (lo, hi, vram)):
            result.append((lo, hi, vram))
            continue
        sites = [set(), set()]
        end_vram = expected+hi-lo
        for start, end, entry in callers:
            targets = jal_targets(struct.unpack_from(f'>{(end-start)//4}I', rom, start), entry)
            inside = start == lo and end == hi
            sites[inside].update(t for t in targets if vram <= t < end_vram)

        if all(_supports_mapping(rom, lo, expected, vram, targets) for targets in sites):
            result[-1] = before, hi, left_vram
            log('Recovered a continuous N64 code mapping from calls inside and outside the range.')
        else:
            result.append((lo, hi, vram))
    return result


def _supports_mapping(rom, lo, expected, current, targets):
    def fits(target, base):
        pos = lo+target-base
        word = struct.unpack_from('>I', rom, pos)[0]
        return is_frame(word) or pos >= lo+8 and struct.unpack_from('>I', rom, pos-8)[0] == JR_RA

    matched = sum(fits(t, expected) for t in targets)
    old = sum(fits(t, current) for t in targets)
    return len(targets) >= 8 and matched*4 >= len(targets)*3 and (matched-old)*4 >= len(targets)


GAP_LIMIT = 0x10000  # the longest function a gap between two pieces of one segment can be


def _scheduled_wide_gap(rom, left_start, seam, right_start, mapping):
    """Recognize a missed text run from called entries on its established mapping."""
    import rabbitizer

    from ...arch.mips_code_recovery import _lead
    from ...arch.mips_references import references

    if right_start - seam > 2 * GAP_LIMIT or rom[right_start - 8:right_start - 4] != JR_RA.to_bytes(4, 'big'):
        return None
    entry = next((seam - lead for lead in (4, 8)
                  if seam - lead - 8 >= left_start
                  and rom[seam - lead - 8:seam - lead] == JR_RA.to_bytes(4, 'big') + bytes(4)
                  and _lead(rom, seam - lead, right_start, 'big', 'vr4300') == lead), None)
    if entry is None:
        return None
    calls = {target - mapping for target, _, kind in references(rom, left_start, entry, left_start + mapping)
             if kind == 'call' and entry <= target - mapping < right_start}
    if entry not in calls or any(entry < target <= seam for target in calls) or len(calls) < 8:
        return None
    framed = [target for target in calls if _lead(rom, target, right_start, 'big', 'vr4300') is not None]
    if len(framed) < 6 or max(framed) - seam < (right_start - seam) // 2:
        return None
    if any(target not in framed and rom[target - 8:target - 4] != JR_RA.to_bytes(4, 'big') for target in calls):
        return None
    gap = rom[entry:right_start]
    if bytes(256) in gap or any(not rabbitizer.Instruction(word).isValid()
                                for word, in struct.iter_unpack('>I', gap)):
        return None
    return entry


def close_gaps(rom: bytes, found: list[tuple[int, int, int]], taken=()) -> list[tuple[int, int, int]]:
    """Neighbouring segments placed at the same mapping (address minus ROM offset), joined with what lies between
    them when it ends in a return: one function longer than the search's quiet stretch (2 blocks with no return and
    no stack frame) ends a stretch, and each side was placed alone. An N64 shooter lost 15 functions of 3-20 KB that
    way, its main loop among them, and its port's main thread ended at the first one."""
    out: list[list[int]] = []
    for start, end, vram in sorted(found):
        if out:
            p = out[-1]
            if (vram - start == p[2] - p[0] and 0 < start - p[1] <= 2 * GAP_LIMIT and start % 4 == 0
                    and p[1] % 4 == 0 and not any(a < start and p[1] < b for a, b, _ in taken)):
                gap = rom[p[1]:start]
                if len(gap) <= GAP_LIMIT and JR_RA in struct.unpack(f">{len(gap) // 4}I", gap):
                    p[1] = end
                    continue
                if len(gap) > GAP_LIMIT:
                    entry = _scheduled_wide_gap(rom, p[0], p[1], start, vram - start)
                    if entry is not None:
                        p[1] = entry
                        out.append([entry, start, entry + vram - start])
        out.append([start, end, vram])
    return [tuple(x) for x in out]


def complete_gaps(rom, taken, found, declared=(), entries=(), log=print):
    """`found` with the gaps between code on one mapping scanned as loader banks are (loader_code): a function
    reached only through a pointer the code builds (lui/addiu), a leaf with no frame, a switch, several functions
    in one gap, or an address the game was seen running (`entries`, runtime_entries). A racing game's split left 16
    of them (31 KB) as data, its object handlers among them, so its port called into nothing. Run last, after the
    game has been run for what it loads (loaded_code): what that finds by following calls stays as it was."""
    from .loader_code import complete as loaded_code

    check = getattr(log, 'check_cancel', lambda: None)
    check()
    spans=sorted(list(taken)+list(found))
    banks=mapped_gaps(spans)
    # A gap between code on two mappings (two overlays side by side) has no address the code on both sides agrees
    # on; one where the game was seen running is scanned on the code before it, whose mapping places that address
    # in the gap (a racing game's 0x801D74F4: the first byte after its overlay's last listed function).
    for (s0,e0,v0),(s1,_,v1) in zip(spans,spans[1:]):
        if e0<s1 and v0-s0!=v1-s1 and s1-e0<=0x10000 and any(v0+e0-s0<=a<v0+s1-s0 for a in entries):
            banks.append((e0,s1,v0+e0-s0))
    slack=aligned_gaps(rom,spans,check=check)
    banks+=slack
    table=mapped_data(spans,declared,len(rom))+[dict(rom_start=a,rom_end=b,vram=v) for a,b,v in slack]
    return loaded_code(rom,list(found),sorted(banks),taken,log,table_ranges=table,
                       what='gaps between code on one mapping',entries=entries)


def aligned_gaps(rom,spans,reach=0x100,*,check=lambda:None):
    """(rom_start, rom_end, vram) of each gap between two code ranges on different mappings whose first bytes the
    code before it places a few bytes later than its own mapping does: an overlay's next function starts on an
    alignment boundary (16 bytes), so the bytes after its last listed function sit that much further on. Counted
    only when known code calls at least two frames of the gap at that offset (a call is the evidence, as a gap's
    mapping is otherwise unknown): a racing game's overlay tail of five functions, 5.7 KB with its constants, stayed
    raw bytes because its calls land 0x10 past where the code before it ends."""
    from ...arch.mips_references import references

    gaps=[(e0,s1,v0+e0-s0) for (s0,e0,v0),(s1,_,v1) in zip(spans,spans[1:])
          if e0<s1 and v0-s0!=v1-s1 and s1-e0<=0x10000]
    if not gaps:return []
    targets=set()
    for a,b,v in spans:
        check()
        targets.update(t for t,_,kind in references(rom,a,b,v) if kind=='call')
    out=[]
    for lo,hi,vram in gaps:
        check()
        best=None
        for d in range(0,reach,4):
            hits={t for t in targets if vram+d<=t<vram+d+hi-lo
                  and is_frame(int.from_bytes(rom[lo+t-vram-d:lo+t-vram-d+4],'big'))}
            if len(hits)>=2 and (best is None or len(hits)>best[0]):best=(len(hits),d)
        if best:out.append((lo,hi,vram+best[1]))
    return out


RUNTIME_ENTRIES = "runtime-entries.txt"


def runtime_entries(workdir: Path) -> set[int]:
    """The addresses the game was seen starting to run, in the project's folder (port/wasm/play.py has the port's
    interpreter write them as the game plays, PORT_ENTRIES): one hexadecimal address a line. Evidence of where
    functions start that the code alone may not give (a function only a pointer the game computes reaches)."""
    try:
        text = (Path(workdir) / RUNTIME_ENTRIES).read_text(encoding="utf-8")
    except OSError:
        return set()
    return {int(x, 16) for x in text.split() if re.fullmatch(r"[0-9A-Fa-f]{8}", x)}


def mapped_gaps(spans):
    """(rom_start, rom_end, vram) of each gap between two code ranges that share one ROM-to-RAM mapping (up to
    64 KB): bytes whose address the code on both sides establishes."""
    return [(e0,s1,v0+e0-s0) for (s0,e0,v0),(s1,_,v1) in zip(spans,spans[1:])
            if e0<s1 and v0-s0==v1-s1 and s1-e0<=0x10000]


def mapped_data(spans,declared,size):
    """Where a switch's jump table may be read (mips_indirect.jump_table): the config's own segments, the code,
    and the bytes after each code range up to the next, on its mapping (an overlay's read-only data follows its
    code, loaded with it). A table counts only when every entry lands inside the candidate function."""
    ranges=[dict(rom_start=s,rom_end=e,vram=v) for s,e,v in list(spans)+list(declared)]
    for (s0,e0,v0),nxt in zip(spans,list(spans[1:])+[(min(size,spans[-1][1]+0x10000),None,None)] if spans else []):
        if e0<nxt[0]:
            ranges.append(dict(rom_start=e0,rom_end=nxt[0],vram=v0+e0-s0))
            # An overlay's read-only data can also come before its code (a table of jump tables ahead of the next
            # overlay): the same bytes on the code after them, whose own mapping places them. A table counts
            # only when it resolves uniquely, so a mapping that doesn't fit the game adds nothing.
            if nxt[2] is not None and nxt[2]-nxt[0]!=v0-s0:
                ranges.append(dict(rom_start=e0,rom_end=nxt[0],vram=nxt[2]-nxt[0]+e0))
    return ranges


# IPL3 reads the cartridge header entry, masks it to a physical RAM
# address, then writes the PI destination, cartridge offset and length.
_BOOT_DMA = bytes.fromhex(
    '3c0bb000 8d690008 3c0a1fff 354affff 3c01a460 012a4824 ac290000 '
    '3c08a460 8d080010 31080002 5500fffd 3c08a460 '
    '24081000 010b4020 010a4024 3c01a460 ac280004 '
    '3c0a0010 254affff 3c01a460 ac2a000c'
)


def boot_switch_range(image, segments):
    """Expose the IPL3 cartridge mirror only when its DMA is identified."""
    start, end = 0x1000, 0x101000
    if len(image) < end or _BOOT_DMA not in image[0x40:start]:
        return []
    entry = int.from_bytes(image[8:12], 'big')
    if not 0x80000400 <= entry < 0x80800000 or entry % 4:
        return []
    if not any(s.rom_start == start and s.vram == entry for s in segments):
        return []
    return [dict(rom_start=start, rom_end=end, vram=entry, target_prefix=0x80000000, boot_copy=True)]


def relocated_switch_ranges(image, segments):
    """Track the immediate data tail of code ranges that share an overlay load mapping."""
    from . import stack_dma

    ranges = boot_switch_range(image, segments) + stack_dma.ranges(image, segments)
    ordered = sorted(segments,key=lambda segment:segment.rom_start)
    for i,(previous,segment) in enumerate(zip(ordered,ordered[1:]), 1):
        delta = segment.vram-segment.rom_start
        if (previous.rom_end!=segment.rom_start or previous.vram-previous.rom_start!=delta
                or segment.vram&0xe0000000!=0x80000000 or segment.vram<0x80200000):
            continue
        start = segment.rom_end
        limit = ordered[i+1].rom_start if i+1<len(ordered) else len(image)
        end = min(limit,start+0x10000)&~3
        if end>start:
            ranges.append(dict(rom_start=start,rom_end=end,vram=(start+delta)&0x1fffffff,
                               target_prefix=0x80000000,code_delta=delta))
    return ranges


# ---------------------------------------------------------------------------------------------- compressed code

def _looks_like_code(data: bytes) -> bool:
    words = struct.unpack(f">{len(data) // 4}I", data[:len(data) // 4 * 4])
    return sum(w == JR_RA for w in words) >= 16 and sum(is_frame(w) for w in words) >= 16


def _rare(rom: bytes, at: int, *, headers=(6,5,2), minimum=0x1000) -> tuple[bytes, int] | None:
    """Rare's packing at `at`: 0x1172/0x1173, an optional three- or four-byte size, and raw deflate.
    Returns (unpacked, packed length)."""
    for skip in headers:
        expected = int.from_bytes(rom[at+2:at+skip], "big") if skip > 2 else None
        if expected is not None and not minimum <= expected <= 0x1000000:
            continue
        d = zlib.decompressobj(-15)
        view = memoryview(rom)[at+skip:at+skip+0x800000]
        try:
            out = d.decompress(view, (expected+1) if expected is not None else 0x1000000)
        except zlib.error:
            continue
        if not d.eof or len(out) < minimum:
            continue
        if expected is not None and expected != len(out):
            continue
        return out, skip+len(view)-len(d.unused_data)
    return None


def yaz0(data: bytes) -> bytes:
    """Nintendo's Yaz0: "Yaz0", the unpacked size, 8 reserved bytes, then groups of a flag byte and 8 items
    (a literal byte, or a back-reference of 2 or 3 bytes)."""
    size = int.from_bytes(data[4:8], "big")
    out = bytearray()
    src = 16
    while len(out) < size and src < len(data):
        flags = data[src]
        src += 1
        for bit in range(7, -1, -1):
            if len(out) >= size or src >= len(data):
                break
            if flags >> bit & 1:
                out.append(data[src])
                src += 1
                continue
            b1, b2 = data[src], data[src + 1]
            src += 2
            dist = ((b1 & 0x0F) << 8 | b2) + 1
            n = b1 >> 4
            if n == 0:
                n = data[src] + 0x12
                src += 1
            else:
                n += 2
            for _ in range(n):
                out.append(out[-dist])
    return bytes(out)


def _next_marker(rom: bytes, start: int) -> int:
    hits = [h for h in (rom.find(b"\x11\x72", start), rom.find(b"\x11\x73", start)) if h >= 0]
    return min(hits) if hits else -1


def verify_unpacked_analysis(original: bytes, analysis: bytes) -> bool:
    """Check every byte against the original cartridge and its decoded code blocks.

    Formats without a reversible encoder can still establish an exact rebuild:
    the linked analysis must match the independently decoded cartridge and all
    padding before the original bytes are copied to the output.
    """
    blocks = unpack_code(original)
    if not blocks:
        return False
    expected = bytearray(original)
    for _, payload in blocks:
        expected.extend(bytes(-len(expected) % 16 + 0x2000))
        expected.extend(payload)
    return expected == analysis


def unpack_code(rom: bytes) -> list[tuple[int, bytes]]:
    """(rom offset, unpacked bytes) of every compressed block in the ROM that holds code. Perfect Dark packs its game
    code as 4 KiB pages (loaded one at a time through the TLB), one after another: those are joined into one block
    before looking for code in it (one page alone can hold too little to tell)."""
    found = []
    at = _next_marker(rom, 0)
    while at >= 0:
        hit = _rare(rom, at)
        if not hit:
            at = _next_marker(rom, at + 1)
            continue
        parts, end = [hit[0]], at + hit[1]
        while len(parts[-1]) == 0x1000:  # a page: the next one may follow (after a little padding)
            nxt = _next_marker(rom, end)
            if nxt < 0 or nxt - end > 16 or not (page := _rare(rom, nxt)):
                break
            parts.append(page[0])
            end = nxt + page[1]
        blob = b"".join(parts)
        if _looks_like_code(blob):
            found.append((at, blob))
        at = _next_marker(rom, end)
    at = rom.find(b"Yaz0")
    while at >= 0:
        size = int.from_bytes(rom[at + 4:at + 8], "big")
        if 0x1000 <= size <= 0x400000 and not any(rom[at + 8:at + 16]):
            try:
                out = yaz0(rom[at:at + size * 2 + 0x100])
            except IndexError:
                out = b""
            if len(out) == size and _looks_like_code(out):
                found.append((at, out))
        at = rom.find(b"Yaz0", at + 4)
    from . import rnc

    at = rom.find(b"RNC\x01")
    while at >= 0:
        size = int.from_bytes(rom[at + 4:at + 8], "big")
        packed = int.from_bytes(rom[at + 8:at + 12], "big")
        if 0x1000 <= size <= rnc.MAX_OUTPUT and at + rnc.HEADER_BYTES + packed <= len(rom):
            try:
                out = rnc.unpack(rom[at:at + rnc.HEADER_BYTES + packed])
            except rnc.RncError:
                pass
            else:
                if _looks_like_code(out):
                    found.append((at, out))
        at = rom.find(b"RNC\x01", at + 4)
    return sorted(found)


# ---------------------------------------------------------------------------------------------- into splat's config

def add_segments(config: Path, workdir: Path, image: str, log=print, *, skip_replacing_boot=False) -> str:
    """An N64 game's code outside the main segment (n64code.py), added to splat's config as code segments of their
    own. Compressed code (Rare's, Yaz0, RNC method 1) is unpacked first and appended to a working copy of the ROM, which splat then
    reads instead (and an export rebuilds). Code the search can't place is then found by running the game, when the
    reversing emulator is installed (loaded_code.py). Returns the image splat reads."""
    import hashlib

    import yaml

    from . import (
        boot_brs,
        boot_gzip,
        copied_overlays,
        dma_overlays,
        indexed_overlays,
        indexed_runtime_code,
        level_overlay_table,
        named_dma,
        overlays,
        raw_slots,
        resident_deflate,
        shared_dma,
    )

    if result := overlays.add(config, workdir, image, log):
        return result

    original = image
    rom = (workdir / image).read_bytes()
    original_rom = rom
    from .boot_jump import recover as recover_jumped_boot

    recover_jumped_boot(config, rom, log)
    taken = split.code_ranges(config)
    if taken and (result := indexed_runtime_code.add(config,workdir,image,rom,taken,log)):
        return result
    if taken and (result := resident_deflate.add(config,workdir,image,rom,taken,log)):
        return result
    if taken and not skip_replacing_boot and boot_brs.find(rom, taken):
        # The replacement program reuses the boot addresses. Recover the boot
        # code first, before the expanded image introduces overlapping banks.
        base = add_segments(config, workdir, image, log, skip_replacing_boot=True)
        if base == original:
            taken = split.code_ranges(config)
            if result := boot_gzip.add(config, workdir, image, rom, taken, log):
                return result
        return base
    if taken and not skip_replacing_boot and (result := boot_gzip.add(config, workdir, image, rom, taken, log)):
        return result
    if taken and (slots := raw_slots.registered(rom, taken)):
        recovered = raw_slots.recover_registered(rom, slots, taken, log)
        if raw_slots.add(config, rom, recovered, slots, log):
            return original
    if taken and (slots := level_overlay_table.read(rom, taken)):
        recovered = raw_slots.recover_registered(rom, slots, taken, log)
        if helper := level_overlay_table.companion(rom, taken, slots):
            recovered += raw_slots.recover_registered(rom, [helper], taken, log)
        if raw_slots.add(config, rom, recovered, slots, log, preserve_gaps=True):
            return original
    packed = unpack_code(rom)
    locations = []
    if packed:
        work = bytearray(rom)
        for _, data in packed:  # each after a gap, so pieces of different blocks never run together
            work += bytes(-len(work) % 0x10 + 0x2000)
            locations.append(len(work))
            work += data
        work += bytes(-len(work) % 0x10)
        image = f"{Path(image).stem}.unpacked{Path(image).suffix}"
        rom = bytes(work)
        kib = sum(len(d) for _, d in packed) // 1024
        log(f"unpacked {len(packed)} compressed code block{'s' if len(packed) != 1 else ''} ({kib} KiB)")
    known = split.segments_from_config(config)
    found = find_segments(rom, taken, tuple(s.rom_end for s in known), log=log,
                          declared=tuple((s.rom_start, s.rom_end, s.vram) for s in known)) if taken else []
    if not packed and taken and (banks := copied_overlays.read(original_rom, taken)):
        copied_overlays.add(config, original_rom, found, banks, log)
        return original
    if not packed and taken and (banks := indexed_overlays.read(original_rom, taken)):
        resident = indexed_overlays.resident_banks(original_rom, taken, before=banks[0].start)
        copied_overlays.add(config, original_rom, found, resident + banks, log)
        return original
    if not packed and taken and (slots := named_dma.read(original_rom, taken)):
        if shared_dma.add(config, original_rom, taken, found, slots, log):
            return original
    if not packed and taken and (slots := shared_dma.read(original_rom, taken, found, include_inline=False)):
        if shared_dma.add(config, original_rom, taken, found, slots, log):
            return original
    if not packed and taken and (banks := dma_overlays.banks(original_rom)):
        dma_overlays.add(config,workdir,original_rom,found,banks,log)
        return original
    if packed and found:
        from . import rare_overlays
        if result := rare_overlays.add(config,workdir,original,original_rom,packed,found,locations,log):
            return result
    loaded = {}
    if taken:  # what the game loads that the search couldn't place: found by running it (when the emulator's here)
        from .loaded_code import find as run_and_find

        loaded = run_and_find(workdir / original, rom, taken, found, log=log)
        found = sorted(found + loaded["segments"])
        found = sorted(complete_gaps(rom, taken, found, tuple((s.rom_start, s.rom_end, s.vram) for s in known),
                                     runtime_entries(workdir), log=log))
    if not packed and not found and loaded.get('decoded_programs'):
        from . import runtime_zlib
        if result := runtime_zlib.add(config, workdir, original, original_rom, taken,
                                      loaded['decoded_programs'], log):
            return result
    from .boot_mapping import (
        extend_startup_data,
        extend_startup_descriptor,
        extend_startup_trap,
        mark_boot_version,
        recover,
    )
    from .rsp_prefix import trim

    found = trim(rom, found, taken, log)
    found = recover_scheduled_mapping_entries(rom, found, taken, log)
    found = bridge_loaded_prologues(rom, found, loaded.get('segments', ()), taken, log)
    recover(config, rom, found, log)
    extend_startup_trap(config, rom, log)
    extend_startup_descriptor(config, rom, log)
    extend_startup_data(config, rom, log)
    mark_boot_version(config, rom, log)
    if not packed and taken and (slots := shared_dma.read(original_rom, taken, found)):
        if shared_dma.add(config, original_rom, taken, found, slots, log):
            return original
    if not packed and taken and (slots := raw_slots.read(original_rom,taken)):
        if raw_slots.add(config,original_rom,raw_slots.recover_gaps(original_rom,slots,found,taken,log),slots,log):
            return original
    if not found:
        return original
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    if packed:
        (workdir/image).write_bytes(rom)
        cfg.setdefault("options", {})["target_path"] = image
        cfg["sha1"] = hashlib.sha1(rom).hexdigest()
    from . import inferred_overlays

    before_mapping = found
    overlaps = [item for item in loaded.get('overlap_segments', ())
                if not any(a < item[1] and item[0] < b for a,b,_ in (*taken, *found))]
    found = sorted(found + overlaps)
    found, runtime = inferred_overlays.runtime_mappings(rom, found, loaded.get('moved_ranges', ()), taken)
    found = inferred_overlays.extend_adjacent_frame(rom, found)
    found = inferred_overlays.extend_tail_wrapper(rom, found, taken)
    found, banks = inferred_overlays.split_concatenated(rom, found, getattr(log, 'check_cancel', lambda: None),
                                                       taken=taken)
    found, scoped = inferred_overlays.recover(rom, found, taken, getattr(log, 'check_cancel', lambda: None),
                                             changed=runtime | banks | {start for start,_,_ in overlaps})
    # Corrected addresses can expose a called prologue whose frame lies beyond
    # the scanner's original alignment tail.
    from .code_tails import bridge_called_frame
    from .code_tails import complete as complete_tails
    called = set()
    for start,end,entry in (*taken, *found):
        words = struct.unpack_from(f'>{(end-start)//4}I', rom, start)
        called.update(jal_targets(words, entry))
    found = complete_tails(rom, found, taken, called=called)
    bridged = bridge_called_frame(rom, found, taken, called=called)
    if len(bridged) < len(found):
        log(f'Recovered {len(found)-len(bridged)} called code bodies across gaps in their mapping.')
    found = bridged
    # A complete framed tail can overlap a different inferred ROM bank at the
    # same runtime address. Keep both only when exclusive scopes can hold them.
    expanded = complete_tails(rom, found, taken, called=called, allow_inferred_overlap=True)
    expanded_scoped = set(scoped)
    for lo, hi, base in expanded:
        for other, other_end, other_base in expanded:
            if lo != other and base < other_base+other_end-other and other_base < base+hi-lo:
                expanded_scoped.update((lo, other))
    safe_expanded = inferred_overlays.safe_scopes(expanded, expanded_scoped, taken)
    if safe_expanded is not None:
        found, scoped = expanded, safe_expanded
    found = inferred_overlays.drop_pointer_fragments(rom, found, taken)
    scoped &= {start for start,_,_ in found}
    safe = inferred_overlays.safe_scopes(found, scoped, taken)
    if safe is None:
        found, scoped = before_mapping, set()
        log('Kept the original code mappings because independent banks would overlap the global address range.')
    else:
        scoped = safe
    if runtime and scoped:
        log(f'Corrected {len(runtime)} inferred code ranges using RAM-observed bytes and their internal calls.')
    # A run of relocation-section files can be mistaken for one continuous
    # linked image: each trailer shifts the next file's ROM-to-VRAM mapping.
    # Use exact trailer extents and relocation-backed calls, preserving any
    # ranges whose mapping cannot yet be proven.
    if not packed and taken:
        from . import reloc_fragments
        records = reloc_fragments.discover(rom, taken, getattr(log, 'check_cancel', lambda: None))
        if records:
            corrected = reloc_fragments.replace(found, records)
            overlaps = {start for start, end, base in corrected for other, stop, address in corrected
                        if start != other and base < address+stop-other and address < base+end-start}
            corrected_scopes = inferred_overlays.safe_scopes(
                corrected, overlaps | {start for start, _, _ in corrected if start in scoped}, taken)
            if corrected_scopes is not None:
                unknown = sum(base is None and text > 0 and any(lo < start+text and start < hi for lo, hi, _ in found)
                              for start, _, text, base in records)
                found, scoped = corrected, corrected_scopes
                mapped = sum(base is not None for _, _, _, base in records)
                log(f'Recovered {len(records)} relocation-backed fragments, including {mapped} independently linked addresses.')
                if unknown:
                    message = (f'{unknown} relocation-backed fragments retain inferred code addresses without '
                               'enough independent calls to verify their linked bases; coverage needs review.')
                    log(message)
                    coverage = workdir/'analysis-coverage.json'
                    notes = json.loads(coverage.read_text(encoding='utf-8')) if coverage.is_file() else []
                    coverage.write_text(json.dumps(list(dict.fromkeys([*notes, message]))), encoding='utf-8')
            else:
                log('Kept the original code mappings: relocation-backed fragments conflict with resident code.')
    if overlaps:
        import copy
        unscoped_cfg = copy.deepcopy(cfg)
    insert_segments(config, cfg, found, len(rom))
    # The runtime scan proved both the mapping and a called ordered trap table. Splat otherwise emits only
    # the entries it sees called and silently leaves the rest as data.
    if loaded.get('segments'):
        from .loaded_code import trap_symbols

        symbols = trap_symbols(rom, set(loaded['segments']) & set(found))
        if symbols:
            split._add_symbols(workdir / 'symbol_addrs.txt', symbols)
    if scoped:
        cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
        inferred_overlays.scope(cfg, found, scoped)
        if overlaps and not inferred_overlays.scope_resident_bss(cfg):
            log('Kept the original code mappings: the observed overlays conflict with resident code scopes.')
            insert_segments(config, unscoped_cfg, before_mapping, len(rom))
        else:
            config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
        log('Recovered overlapping code-bank addresses from their independent internal calls.')
    kib = sum(en - st for st, en, _ in found) // 1024
    log(f"found {len(found)} more code segment{'s' if len(found) != 1 else ''} the game loads later ({kib} KiB)")
    if not packed and loaded.get('decoded_programs'):
        from . import runtime_zlib
        if result := runtime_zlib.add(config, workdir, original, original_rom, taken+found,
                                      loaded['decoded_programs'], log):
            return result
    return image


def recover_scheduled_mapping_entries(image, found, taken, log=print):
    """Move an inferred split from a stack allocation to its independently called prologue."""
    from ...arch.mips_code_recovery import _lead

    original = sorted(found)
    result = list(original)
    for index, (left, right) in enumerate(zip(original, original[1:])):
        start, end, first_address = left
        seam, stop, address = right
        if end != seam or seam-start < 16 or address != first_address+seam-start:
            continue
        if not is_frame(int.from_bytes(image[seam:seam+4], 'big')):
            continue
        for lead in (4, 8):
            entry = seam-lead
            if (image[entry-8:entry] != JR_RA.to_bytes(4, 'big') + bytes(4)
                    or _lead(image, entry, stop, 'big', 'vr4300') != lead):
                continue
            candidates = range(address-lead, address+4, 4)
            targets = {0x0c000000 | ((target>>2)&0x03ffffff):target for target in candidates}
            called = set()
            for lo, hi, vram in (*taken, *original):
                if lo%4 or hi%4 or not 0 <= lo < hi <= len(image) or (vram>>28) != address>>28:
                    continue
                words = (word for word, in struct.iter_unpack('>I', memoryview(image)[lo:hi]))
                called.update(targets[word] for word in words if word in targets)
            if (called != {address-lead}
                    or any(target.to_bytes(4,'big') in image for target in range(address-lead+4,address+4,4))):
                continue
            result[index] = (result[index][0], entry, result[index][2])
            result[index+1] = (entry, stop, address-lead)
            log('Kept a directly called scheduled prologue with its recovered code range.')
            break
    return result


def bridge_loaded_prologues(image, found, loaded, taken=(), log=print):
    """Keep a two-instruction address load with the adjacent RAM-observed frame."""
    from ...arch.mips_references import references

    original = sorted(found)
    result = list(original)
    loaded_starts = {start for start, _, _ in loaded}
    for index, (left, right) in enumerate(zip(original, original[1:])):
        start, end, base = left
        seam, stop, address = right
        if (seam not in loaded_starts or seam - end != 4 or end - start < 12
                or address - seam != base - start or not is_frame(int.from_bytes(image[seam:seam+4], 'big'))):
            continue
        entry = end - 4
        ret, pop, upper, load = struct.unpack_from('>4I', image, entry - 8)
        reg = (upper >> 16) & 31
        if (ret != JR_RA or pop >> 16 != 0x27bd or not 0 < (pop & 0xffff) < 0x8000
                or upper >> 26 != 15 or not reg or load >> 26 != 35
                or (load >> 21) & 31 != reg or (load >> 16) & 31 != reg):
            continue
        interior = {address - 4, address}
        if any(target in interior for lo, hi, vram in (*taken, *original)
               for target, _, _ in references(image, lo, hi, vram)):
            continue
        if any(target.to_bytes(4, 'big') in image for target in interior):
            continue
        result[index] = (start, entry, base)
        result[index+1] = (entry, stop, address - 8)
        log('Kept a scheduled address load with its RAM-observed code range.')
    return result


def insert_segments(config: Path, cfg: dict, found, size: int, *, scopes=None) -> None:
    """Write `found` (rom_start, rom_end, vram) into splat's config `cfg` as code segments of their own, each
    followed by raw bytes up to the next segment, and save it to `config`. Used by the import, and by
    missed_functions on a project that already exists."""
    import yaml

    # Several import paths also discover code in a raw child of a code parent. Keep it in that
    # parent: a top-level insertion would cut off the parent's later children and lose their mappings.
    parents = split.segments_from_config_data(cfg)
    children, outside, recovered_tails = [], [], []
    for body in found:
        lo, hi, _ = body
        overlaps = [s for s in parents if s.rom_start < hi and lo < s.rom_end]
        if overlaps:
            parent = overlaps[0]
            if len(overlaps) == 1 and parent.rom_start <= lo < hi <= parent.rom_end and _cut_ok(cfg, body):
                children.append(body)
                continue
            if len(overlaps) == 1 and _replaceable_parent_tail(cfg, body):
                raw = next(s for s in cfg['segments'] if isinstance(s, dict) and s.get('start') == parent.rom_start)
                source = max((s for s in raw['subsegments'] if isinstance(s, list) and len(s) >= 2 and s[0] <= lo),
                             key=lambda s: s[0])
                recovered_tails.append((body, raw, source[1]))
                continue
            raw = next((s for s in cfg.get('segments', []) if isinstance(s, dict)
                        and s.get('start') == parent.rom_start and s.get('type') == 'code'), {})
            later = any(isinstance(s, list) and s and s[0] >= lo for s in raw.get('subsegments', []))
            if len(overlaps) == 1 and parent.rom_start < lo < parent.rom_end < hi and not later:
                # Import can replace a guessed tail when no configured child would be lost.
                outside.append(body)
            else:
                raise RomError('Recovered code crosses an existing bank or has a different mapping. Review it before importing.')
        else:
            outside.append(body)
    # Keep discoveries in their existing parent: separate top-level banks at
    # guessed boundaries can cut one real function across export parents.
    for (lo, _, _), parent, _ in recovered_tails:
        child = next((s for s in parent['subsegments'] if isinstance(s, list) and s and s[0] == lo), None)
        if child is None:
            parent['subsegments'].append([lo, 'bin'])
        else:
            child[1] = 'bin'
    if children or recovered_tails:
        _cut_inside(cfg, sorted(children + [body for body, _, _ in recovered_tails]))
    starts = {body[0] for body, _, _ in recovered_tails}
    for (_, hi, _), parent, kind in recovered_tails:
        if hi not in starts:
            child = next((s for s in parent['subsegments'] if isinstance(s, list) and s and s[0] == hi), None)
            if child is not None and child[1] == 'bin':
                child[1] = kind
    found = outside

    def start_of(seg):
        return seg[0] if isinstance(seg, list) else seg.get("start")

    # splat measures a parent's VRAM end as ROM length + bss_size. New code segments can split its data
    # tail: remember that end so the explicit BSS still describes the same addresses after the split.
    bss_ends = {}
    for seg in split.segments_from_config(config):
        raw = next(s for s in cfg["segments"] if isinstance(s, dict) and s.get("start") == seg.rom_start)
        if raw.get("bss_size") and any(isinstance(sub, dict) and sub.get("type") == "bss" and "vram" in sub
                                       for sub in raw.get("subsegments", [])):
            bss_ends[seg.rom_start] = seg.vram + seg.rom_end - seg.rom_start + raw["bss_size"]

    scopes = scopes or {}
    starts = {st for st, _, _ in found}
    names = {s.get('name') for s in cfg['segments'] if isinstance(s, dict) and s.get('name')}
    # A discovered code span can straddle an old bin marker created by splat.
    # Keeping it would cut the verified span and truncate an otherwise complete function.
    segs = [s for s in cfg["segments"] if not (isinstance(s, dict) and s.get("type") == "bin"
            and start_of(s) is not None and any(st <= start_of(s) < en for st, en, _ in found))
            and not isinstance(s, list)]  # the end marker comes back at the image's end
    for st, en, vram in found:
        duplicate = sum(base == vram for _, _, base in found) > 1
        name = f"code_{vram:08X}_{st:X}" if duplicate else f"code_{vram:08X}"
        if name in names:
            name = f"code_{vram:08X}_{st:X}"
        if name in names:
            raise ValueError(f"A code segment named {name} already exists")
        names.add(name)
        segment = {"name": name, "type": "code", "start": st, "vram": vram, "subsegments": [[st, "c", name]]}
        if scope := scopes.get(st):
            segment.update(exclusive_ram_id=scope, symbol_name_format=f'$VRAM_{name}',
                           symbol_name_format_no_rom=f'$VRAM_{name}')
        segs.append(segment)
        if en < size and en not in starts and not any(start_of(s) == en for s in segs if not isinstance(s, list)):
            segs.append({"type": "bin", "start": en})
    segs.sort(key=lambda s: start_of(s) if start_of(s) is not None else -1)
    segs.append([size])
    for i, seg in enumerate(segs[:-1]):
        start = start_of(seg)
        if start in bss_ends:
            end = next(start_of(s) for s in segs[i + 1:] if start_of(s) is not None)
            seg["bss_size"] = bss_ends[start] - seg["vram"] - (end - start)
    cfg["segments"] = segs
    config.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None), encoding="utf-8")


def missed_functions(config: Path, rom: bytes, log=print, known_functions=(), *, prepare_split=None,
                     checkpoint=None, expected_config=None) -> list[tuple[int, int, int]]:
    """In a project that exists, the functions its split left as data that an import would now find (complete_gaps,
    with the addresses the game was seen running in the project's folder): written into splat's `config` as code
    segments, as an import writes them, and returned as (rom_start, rom_end, vram), one function each. Lets a
    project gain them without being imported again, which would reopen its work."""
    import yaml

    initial_text = config.read_text(encoding='utf-8')
    if expected_config is not None and initial_text != expected_config:
        raise RomError('The split configuration changed before the search began. Retry with the current project.')
    taken = split.code_ranges(config)
    if not taken:
        return []
    known = split.segments_from_config(config)
    declared = tuple((s.rom_start, s.rom_end, s.vram) for s in known)
    found = complete_gaps(rom, taken, [], declared, runtime_entries(config.parent), log=log)
    cfg = yaml.safe_load(initial_text)
    inside = _bodies_inside_code(cfg, rom, taken, known_functions, log) if known_functions else []
    # Existing projects can predate the runtime import scan. A mapped trap table contains independently
    # bounded entries even when most are never called during the sample run. Other runtime code ranges
    # need function-boundary analysis before they can be added here.
    from . import emulator, loaded_code, runtime_scoped

    image = config.parent / (cfg.get('options', {}).get('target_path') or 'rom.z64')
    samples = []
    if emulator.installed() and image.is_file() and image.read_bytes() == rom:
        log('Running the game to check for directly called code missing from its mappings.')
        cancel = getattr(log, 'cancel_event', None)
        reviews = []

        def inspect(dumps):
            reviews.extend(loaded_code.moved_banks(rom, declared, found + inside, known_functions, dumps,
                                                   getattr(log, 'check_cancel', lambda: None)))
            if any(s.exclusive_ram_id for s in known):
                samples.append(dumps)

        observed = loaded_code.find(image, rom, taken, found, log=log,
                                    stop=cancel.is_set if cancel is not None else lambda: False, inspect_dumps=inspect)
        getattr(log, 'check_cancel', lambda: None)()
        if any(0x80400000 <= vram < 0x80800000 for _, _, vram in found + inside):
            log('Checking the proposed high-memory code with an Expansion Pak as well.')
            loaded_code.find(image, rom, taken, found, log=log,
                             stop=cancel.is_set if cancel is not None else lambda: False, inspect_dumps=inspect,
                             expansion_pak=True, vis=1800, dumps_at=(*loaded_code.DUMPS, 1500, 1790))
            getattr(log, 'check_cancel', lambda: None)()
        reviews = sorted(set(reviews))
        for (start, end, address), shift, count in reviews:
            log(f'Deferred code in ROM {start:#x}–{end:#x}: {count} bodies were also observed at '
                f'{start + shift:#x}, while its existing mapping is {address:#x}. Review the bank before adding them.')
        if reviews:
            def safe(body):
                return not any(start <= body[0] < body[1] <= end and address + body[0] - start == body[2]
                               for (start, end, address), _, _ in reviews)
            found = [body for body in found if safe(body)]
            inside = [body for body in inside if safe(body)]
        for start, end, vram in observed['segments']:
            if loaded_code.trap_symbols(rom, [(start, end, vram)]):
                found.extend((at, at + 8, vram + at - start) for at in range(start, end, 8))
    new = sorted({r for r in found if not any(a < r[1] and r[0] < b for a, b, _ in taken)})
    # A raw child of a code parent is absent from code_ranges, but it is still inside that parent.
    # Inserting another top-level segment there truncates the parent's later children and breaks its split.
    children = [body for body in new if _owner(cfg, body[0], body[1]) is not None
                and not _replaceable_parent_tail(cfg, body)]
    cuttable = [body for body in children if _cut_ok(cfg, body)]
    rejected = len(children) - len(cuttable)
    if rejected:
        log(f'Deferred {rejected} candidate bodies whose parent mapping or raw boundary needs review.')
    inside = sorted(set(inside + cuttable))
    new = [body for body in new if body not in children]
    ambiguous = [body for body in new if any(
                     s.exclusive_ram_id and s.vram - s.rom_start == body[2] - body[0] or
                     s.vram - s.rom_start != body[2] - body[0] and
                     s.vram < body[2] + body[1] - body[0] and body[2] < s.vram + s.rom_end - s.rom_start
                     for s in known) and not runtime_scoped.inherited_scope(body, known)]
    if ambiguous:
        log(f'Deferred {len(ambiguous)} candidate bodies whose overlay scope is ambiguous.')
        new = [body for body in new if body not in ambiguous]
    inherited = [dict(rom_start=lo, rom_end=hi, vram=vram, scope=scope)
                 for lo, hi, vram in new if (scope := runtime_scoped.inherited_scope((lo, hi, vram), known))]
    scoped = runtime_scoped.closure(rom, known, known_functions, samples, reserved=new + inside,
                                    check=getattr(log, 'check_cancel', lambda: None)) if samples else []
    bodies = [(e['rom_start'], e['rom_end'], e['vram']) for e in scoped]
    candidates = [dict(rom_start=lo, rom_end=hi, vram=vram) for lo, hi, vram in sorted(set(new + inside + bodies))]
    if candidates:
        import tempfile

        from . import runtime_staging

        log(f'Checking {len(candidates)} recovered bodies in a temporary split.')
        with tempfile.TemporaryDirectory(prefix='decomp-overlay-config-') as tmp:
            staged = Path(tmp) / 'game.yaml'
            scopes = {e['rom_start']: e['scope'] for e in inherited + scoped}
            original = initial_text
            new_bodies = set(new + bodies)
            inside_bodies = set(inside)
            baseline_failed = False
            baseline_cache = {}
            verified_anchors = {}

            def verify(selected, *, quiet=False):
                nonlocal baseline_failed
                baseline_failed = False
                if config.read_text(encoding='utf-8') != original:
                    raise RomError('The split configuration changed during the search. Retry with the current project.')

                def failed(before):
                    nonlocal baseline_failed
                    baseline_failed = before

                selected = sorted(selected, key=lambda item: (item['rom_start'], item['vram']))
                chosen = {(e['rom_start'], e['rom_end'], e['vram']) for e in selected}
                staged.write_text(original, encoding='utf-8')
                proposed = yaml.safe_load(original)
                if inserts := sorted(new_bodies & chosen):
                    insert_segments(staged, proposed, inserts, len(rom), scopes=scopes)
                    proposed = yaml.safe_load(staged.read_text(encoding='utf-8'))
                if cuts := sorted(inside_bodies & chosen):
                    _cut_inside(proposed, cuts)
                anchors, attempted, conflicts, errors = [], set(), [], []

                def check_split():
                    conflicts.clear()
                    return runtime_staging.verify(config, rom, proposed, selected, log, prepare=prepare_split,
                                                  quiet=True, on_failure=failed, cache=baseline_cache,
                                                  on_conflict=conflicts.extend, on_error=errors.append)

                okay = check_split()
                while not okay and conflicts and not baseline_failed:
                    # A new file boundary can make Splat read later existing functions as one data block.
                    # Anchor the first affected entry per source unit, then verify every old and new body again.
                    first = {}
                    for row in sorted(conflicts, key=lambda item: item['rom_offset']):
                        body = (row['rom_offset'], row['rom_offset'] + row['size'], row['vram'])
                        owner = _owner(proposed, body[0], body[1])
                        if body in attempted or owner is None or not _cut_ok(proposed, body):
                            continue
                        subs = [s for s in owner.get('subsegments', []) if isinstance(s, list) and len(s) >= 2
                                and s[0] <= body[0]]
                        if not subs:
                            continue
                        unit = max(subs, key=lambda sub: sub[0])[0]
                        first.setdefault((owner['start'], unit), body)
                    changed = False
                    for body in first.values():
                        attempted.add(body)
                        if _cut_inside(proposed, [body]):
                            anchors.append(body)
                            changed = True
                    if not changed:
                        break
                    okay = check_split()
                if okay:
                    verified_anchors[tuple(sorted(chosen))] = tuple(anchors)
                elif not quiet:
                    detail = errors[-1] if errors else 'the staged split could not be verified'
                    log(f'Recovered candidates remain for review: {detail}.')
                return okay

            selected = candidates
            if not verify(candidates):
                # One ambiguous body must not block every other bank. Check all groups and subdivide a
                # failed one down to individual bodies; each accepted set is checked together before use.
                if baseline_failed:
                    selected, deferred = [], candidates
                else:
                    fingerprint = hashlib.sha256(rom)
                    fingerprint.update(hashlib.sha256(original.encode('utf-8')).digest())
                    inputs = json.dumps({'known': sorted(known_functions, key=lambda item: item[0]),
                                         'candidates': candidates}, sort_keys=True).encode('utf-8')
                    fingerprint.update(hashlib.sha256(inputs).digest())
                    fingerprint_key = fingerprint.hexdigest()
                    by_identity = {(e['rom_start'], e['rom_end'], e['vram']): e for e in candidates}
                    cached, cached_ids = [], set()
                    if checkpoint:
                        saved = checkpoint['load'](fingerprint_key)
                        if isinstance(saved, list):
                            for item in saved:
                                if isinstance(item, list) and len(item) == 3 and all(type(value) is int for value in item):
                                    identity = tuple(item)
                                    if identity in by_identity and identity not in cached_ids:
                                        cached_ids.add(identity)
                                        cached.append(by_identity[identity])
                    if cached and not verify(cached, quiet=True):
                        if baseline_failed:
                            raise RomError('The original split could no longer be checked. Retry the search.')
                        cached = []
                        cached_ids = set()
                    if cached:
                        log(f'Resuming with {len(cached)} previously checked bodies, verified against the current split.')
                    groups = {}
                    for item in candidates:
                        body = (item['rom_start'], item['rom_end'], item['vram'])
                        if body in cached_ids:
                            continue
                        owner = _owner(cfg, body[0], body[1]) if body in inside_bodies else None
                        key = ('inside', owner['start']) if owner else ('new', body[2] - body[0], scopes.get(body[0], ''))
                        groups.setdefault(key, []).append(item)
                    log('Checking the proposed banks separately to retain every safe recovery.')

                    def verify_group(group):
                        okay = verify(group, quiet=True)
                        if baseline_failed:
                            raise RomError('The original split could no longer be checked. Retry the search.')
                        return okay

                    def save_checked(items):
                        if checkpoint:
                            checkpoint['save'](fingerprint_key, [[e['rom_start'], e['rom_end'], e['vram']] for e in items])

                    selected, deferred = runtime_staging.isolate(list(groups.values()), verify_group, log,
                                                                  initial=cached, checkpoint=save_checked)
                if deferred:
                    log(f'{len(deferred)} candidate bod{"y" if len(deferred) == 1 else "ies"} could not be added safely '
                        f'and remain{"s" if len(deferred) == 1 else ""} for review.')
            chosen = {(e['rom_start'], e['rom_end'], e['vram']) for e in selected}
            anchors = verified_anchors.get(tuple(sorted(chosen)), ())
            new = sorted(new_bodies & chosen)
            inside = sorted(inside_bodies & chosen)
            if checkpoint and not baseline_failed:
                checkpoint['finished'] = True
    else:
        scopes = {}
        anchors = ()
        if checkpoint:
            checkpoint['finished'] = True
    if not new and not inside:
        return []
    import tempfile

    # Build the complete configuration away from the project. A cancelled scan or a concurrent edit must
    # not leave half its cuts behind or overwrite a newer configuration.
    with tempfile.TemporaryDirectory(prefix='decomp-missed-config-') as tmp:
        staged = Path(tmp) / 'game.yaml'
        staged.write_text(initial_text, encoding='utf-8')
        cfg = yaml.safe_load(initial_text)
        if new:
            insert_segments(staged, cfg, new, len(rom), scopes=scopes)
            cfg = yaml.safe_load(staged.read_text(encoding='utf-8'))
        if inside:
            _cut_inside(cfg, inside)
        for anchor in anchors:
            _cut_inside(cfg, [anchor])
        result = sorted(set(new) | {b for b in inside if _cut_ok(cfg, b)})
        text = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None)
    getattr(log, 'check_cancel', lambda: None)()
    if config.read_text(encoding='utf-8') != initial_text:
        raise RomError('The split configuration changed during the search. Retry with the current project.')
    split.write_config_atomic(config, text)
    return result


def _bodies_inside_code(cfg, rom, taken, known, log):
    """Closed bodies in a code segment's raw bytes: the gaps between the project's own functions, scanned as
    loader banks are (loader_code). The split left them as `.word` data, and an import now recovers them (mixed_code)
    but a project only gained them by being imported again."""
    from .loader_code import complete

    spans = sorted((lo, hi, vram) for lo, hi, vram in known)
    banks = [(a, b, v) for a, b, v in taken]
    table = [dict(rom_start=a, rom_end=b, vram=v) for a, b, v in banks]
    found = complete(rom, spans, banks, (), log, table_ranges=table, what="the gaps inside code segments")
    return sorted(set(found) - set(spans))


def _owner(cfg, lo, hi):
    """The splat segment (with C subsegments) whose bytes hold [lo, hi), or None."""
    segs = [x for x in cfg.get("segments", []) if (isinstance(x, dict) and "start" in x) or isinstance(x, list)]
    start_of = lambda x: x["start"] if isinstance(x, dict) else x[0]
    segs.sort(key=start_of)
    for seg, after in zip(segs, segs[1:]):
        if isinstance(seg, dict) and isinstance(seg.get("subsegments"), list) and start_of(seg) <= lo and hi <= start_of(after):
            return seg
    return None


def _cut_ok(cfg, body):
    lo, hi, vram = body
    seg = _owner(cfg, lo, hi)
    if (seg is None or seg.get('type') != 'code' or not isinstance(seg.get('vram'), int)
            or seg['vram'] + lo - seg['start'] != vram):
        return False
    subs = [s for s in seg.get('subsegments', []) if isinstance(s, list) and len(s) >= 2]
    containing = max((s for s in subs if s[0] <= lo), key=lambda s: s[0], default=None)
    return (containing is not None and containing[1] in ('c', 'bin') and
            not any(lo < s[0] < hi and s[1] != containing[1] for s in subs))


def _replaceable_parent_tail(cfg, body):
    """A guessed data tail may be split when it has no later ROM-backed children to lose."""
    lo, hi, vram = body
    seg = _owner(cfg, lo, hi)
    if (seg is None or seg.get('type') != 'code' or not isinstance(seg.get('vram'), int)
            or seg['vram'] + lo - seg['start'] != vram):
        return False
    subs = [s for s in seg.get('subsegments', []) if isinstance(s, list) and len(s) >= 2]
    source = max((s for s in subs if s[0] <= lo), key=lambda s: s[0], default=None)
    return (source is not None and source[1] in ('data', '.data', 'rodata', '.rodata')
            and not any(s[0] > lo or s[0] == lo and s is not source for s in subs))


def _cut_inside(cfg, bodies):
    """Give each body its own C subsegment (cuts at its start and end) in the code segment that holds it."""
    changed = False
    # Pick the original parent, not a subsegment created for an earlier body. Repeatedly appending to the
    # newest name makes a long run of recovered functions exceed the filesystem's filename limit.
    originals = {id(seg): [list(s) for s in seg.get('subsegments', []) if isinstance(s, list) and len(s) >= 2]
                 for seg in cfg.get('segments', []) if isinstance(seg, dict)}
    ordered = [s for s in cfg.get('segments', [])
               if (isinstance(s, list) and s) or (isinstance(s, dict) and 'start' in s)]
    ordered.sort(key=lambda s: s[0] if isinstance(s, list) else s['start'])
    ends = {id(seg): (after[0] if isinstance(after, list) else after['start'])
            for seg, after in zip(ordered, ordered[1:]) if isinstance(seg, dict)}
    for lo, hi, vram in bodies:
        if not _cut_ok(cfg, (lo, hi, vram)):
            continue
        seg = _owner(cfg, lo, hi)
        if seg is None:
            continue
        subs = seg["subsegments"]
        existing = {s[0] for s in subs if isinstance(s, list) and s}
        source = max((s for s in originals[id(seg)] if s[0] <= lo), key=lambda s: s[0], default=None)
        parent = max((s for s in originals[id(seg)] if s[0] <= lo and s[1] == 'c' and len(s) > 2),
                     key=lambda s: s[0], default=None)
        if source is None:
            continue
        for at in (lo, hi):
            if at == hi and at == ends.get(id(seg)):
                continue  # The next parent already closes this segment; a child there is out of bounds.
            previous = next((s for s in subs if isinstance(s, list) and s and s[0] == at), None)
            if previous is None or at == lo and previous[1] == 'bin':
                prefix = str(parent[2] if parent else seg.get('name', 'code'))
                directory, _, basename = prefix.rpartition('/')
                basename = basename[:180]
                stem = f'{basename}_{at:06X}'
                name = f'{directory}/{stem}' if directory else stem
                used = {s[2] for s in subs if isinstance(s, list) and len(s) > 2 and isinstance(s[2], str)}
                suffix = 1
                while name in used:
                    name = f'{directory}/{stem}_{suffix}' if directory else f'{stem}_{suffix}'
                    suffix += 1
                kind = source[1] if at == hi else 'c'
                if previous is None:
                    subs.append([at, kind, name])
                else:
                    previous[1:] = [kind, name]
                existing.add(at)
                changed = True
        def offset(sub, parent=seg):
            if isinstance(sub, list) and sub:
                return sub[0]
            if isinstance(sub, dict):
                if isinstance(sub.get('start'), int):
                    return sub['start']
                if isinstance(sub.get('vram'), int) and isinstance(parent.get('vram'), int):
                    return parent['start'] + sub['vram'] - parent['vram']
            return float('inf')
        # A BSS child has a VRAM but no ROM start. Sorting it as offset zero puts it
        # before code and makes Splat reject the otherwise valid staged split.
        subs.sort(key=offset)
    return changed


def recover_terminal_display_lists(result, image, log=print):
    """Classify a display-list call and its neighboring asset name as data."""
    from ...arch.mips_references import references

    candidates = []
    for fn in result.functions:
        if fn.size != 8 or fn.lines[0].word != 0xde000000:
            continue
        end = fn.rom+8
        pointer = fn.lines[1].word
        tail = image[end:end+32]
        if (0x80000400 <= pointer < 0x80800000 and not pointer%8 and fn.rom >= 12
                and any(s.rom_end == end and s.rom_start <= fn.rom-12
                        and s.vram-s.rom_start == fn.vram-fn.rom for s in result.segments)
                and struct.unpack_from('>3I',image,fn.rom-12) == (JR_RA,0,0)
                and re.fullmatch(rb'\0{4}[A-Z][A-Z0-9_ ]{5,19}\0{4,}',tail)):
            candidates.append(fn)
    if not candidates:
        return
    targets = set(result.named.values())
    for segment in result.segments:
        targets.update(target for target,_,_ in references(image,segment.rom_start,segment.rom_end,segment.vram))
    removed = set()
    for fn in candidates:
        if any(address in targets or address.to_bytes(4,'big') in image for address in (fn.vram,fn.vram+4)):
            continue
        removed.add((fn.rom,fn.vram))
        result.data.append(split.DataSymbol(fn.name,fn.vram,fn.rom,8,'data'))
        log('Recovered a display-list command at the end of a code range.')
    result.functions = [fn for fn in result.functions if (fn.rom,fn.vram) not in removed]
