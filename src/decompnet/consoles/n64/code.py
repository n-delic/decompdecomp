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
import struct
import zlib
from pathlib import Path

from ...arch.mips import JR_RA, SCAN_BLOCK, code_before, is_frame, jal_targets, place
from ...rom import split

BLOCK = SCAN_BLOCK
# where code can run: KSEG0 (RAM), or mapped through the TLB (GoldenEye's game code runs at 0x7F000000). A jal keeps
# the top four bits of the address it's at, so the region decides what its targets are.
REGIONS = ((0x80000000, 0x81000000), (0x70000000, 0x80000000))
KSEG0 = REGIONS[0][0]  # where the known code (the main segment) runs


def _code_like(words: tuple[int, ...]) -> bool:
    """A return or a stack frame: either is rare in anything but code (a large function can go a while without the
    other)."""
    return any(w == JR_RA or is_frame(w) for w in words)


def find_segments(rom: bytes, taken: list[tuple[int, int, int]], ends: tuple[int, ...] = ()) -> list[tuple[int, int, int]]:
    """(rom_start, rom_end, vram) of code segments outside `taken` (rom_start, rom_end, vram of the known ones).
    `ends`: where the known segments end, their data included (splat's config): code found after one can begin
    right there."""
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
            if seg:
                candidates.append(seg)
                floor = seg[1]
    found = []
    used_vram = [(v, v + (e - s)) for s, e, v in taken]

    def accept(start: int, end: int, vram: int) -> bool:
        if any(a < vram + (end - start) and vram < b for a, b in used_vram) or \
                any(a < end and start < b for a, b, _ in found):
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
    return sorted(found)


# ---------------------------------------------------------------------------------------------- compressed code

def _looks_like_code(data: bytes) -> bool:
    words = struct.unpack(f">{len(data) // 4}I", data[:len(data) // 4 * 4])
    return sum(w == JR_RA for w in words) >= 16 and sum(is_frame(w) for w in words) >= 16


def _rare(rom: bytes, at: int) -> tuple[bytes, int] | None:
    """Rare's packing at `at`: 0x1172/0x1173, maybe the unpacked size (3 bytes), then a raw deflate stream.
    Returns (unpacked, packed length)."""
    for skip in (5, 2):
        d = zlib.decompressobj(-15)
        try:
            out = d.decompress(rom[at + skip:at + skip + 0x800000], 0x1000000)
        except zlib.error:
            continue
        if not d.eof or len(out) < 0x1000:
            continue
        if skip == 5 and int.from_bytes(rom[at + 2:at + 5], "big") != len(out):
            continue
        return out, skip + len(rom[at + skip:at + skip + 0x800000]) - len(d.unused_data)
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
    return sorted(found)


# ---------------------------------------------------------------------------------------------- into splat's config

def add_segments(config: Path, workdir: Path, image: str, log=print) -> str:
    """An N64 game's code outside the main segment (n64code.py), added to splat's config as code segments of their
    own. Compressed code (Rare's, Yaz0) is unpacked first and appended to a working copy of the ROM, which splat then
    reads instead (and an export rebuilds). Returns the image splat reads."""
    import hashlib

    import yaml

    original = image
    rom = (workdir / image).read_bytes()
    packed = unpack_code(rom)
    if packed:
        work = bytearray(rom)
        for _, data in packed:  # each after a gap, so pieces of different blocks never run together
            work += bytes(-len(work) % 0x10 + 0x2000)
            work += data
        work += bytes(-len(work) % 0x10)
        image = f"{Path(image).stem}.unpacked{Path(image).suffix}"
        (workdir / image).write_bytes(work)
        rom = bytes(work)
        kib = sum(len(d) for _, d in packed) // 1024
        log(f"unpacked {len(packed)} compressed code block{'s' if len(packed) != 1 else ''} ({kib} KiB)")
    taken = split.code_ranges(config)
    found = find_segments(rom, taken, tuple(s.rom_end for s in split.segments_from_config(config))) if taken else []
    if not found:
        return original
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    if packed:
        cfg.setdefault("options", {})["target_path"] = image
        cfg["sha1"] = hashlib.sha1(rom).hexdigest()

    def start_of(seg):
        return seg[0] if isinstance(seg, list) else seg.get("start")

    starts = {st for st, _, _ in found}
    segs = [s for s in cfg["segments"] if not (isinstance(s, dict) and s.get("type") == "bin" and start_of(s) in starts)
            and not isinstance(s, list)]  # the end marker comes back at the image's end
    for st, en, vram in found:
        name = f"code_{vram:08X}"
        segs.append({"name": name, "type": "code", "start": st, "vram": vram, "subsegments": [[st, "c", name]]})
        if en < len(rom) and not any(start_of(s) == en for s in segs if not isinstance(s, list)):
            segs.append({"type": "bin", "start": en})
    segs.sort(key=lambda s: start_of(s) if start_of(s) is not None else -1)
    segs.append([len(rom)])
    cfg["segments"] = segs
    config.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None), encoding="utf-8")
    kib = sum(en - st for st, en, _ in found) // 1024
    log(f"found {len(found)} more code segment{'s' if len(found) != 1 else ''} the game loads later ({kib} KiB)")
    return image
