"""Code the game loads at run time, found by running it: where the static search (code.py) couldn't place it.

code.py places code it finds in the ROM by its calls: the load address that makes the most calls land on stack-frame
starts. A game whose overlays are small, each with its own load address, defeats that: a racing game had 48
functions its known code calls in no segment, its main thread's code among them, so a port stopped there. Running
the game settles it with no guessing. The reversing emulator (consoles/n64/emulator, when installed) runs the ROM
headless for a few seconds, pressing Start and A now and then to get past the title, and writes RAM a few times. A
function the known code calls that's code in RAM there, byte for byte a stretch of the ROM, gives its segment
exactly: that stretch's ROM range and the address it's loaded at. Each segment found is followed for its own calls.

Only what the run loads is found (a later track's overlay needs a run that gets there), and only code the ROM holds
as it is: code a game decodes as it loads is counted and reported (`summary`), not added. A known segment the game
runs at another address than the split placed it is reported with its verified ROM and RAM range. A fresh import
can use the range when independent internal calls also support its mapping. When cross-bank calls repeatedly appear
to enter framed bodies, a later no-input run can recover a second overlapping bank if several captured callers and
targets independently match the same ROM-to-RAM mapping.
"""

from __future__ import annotations

import struct
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from ...arch.mips import JR_RA, is_frame, jal_targets

VIS = 1200  # 20 seconds of the game (PAL 24): about two seconds to run
DUMPS = (60, 300, 600, 900, 1200)
CHUNK = 128  # the RAM a function starts with, found once in the ROM, ties RAM to ROM
RAM = (0x80000400, 0x80800000)
_TRAP_JUMP = b'\x00\x00\x00\x08'  # jr $zero: a game's exception-dispatched call table
_TRAP_MIN_ENTRIES = 8


def _trap_id(data, at):
    if at < 0 or at + 8 > len(data) or data[at:at + 4] != _TRAP_JUMP:
        return None
    delay = int.from_bytes(data[at + 4:at + 8], 'big')
    if delay == 0x00004025:  # move $t0, $zero, for the first entry
        return 0
    return delay & 0xffff if delay >> 16 == 0x2408 else None  # addiu $t0, $zero, id


def _next_trap_id(previous, current):
    return current > previous and (current == previous + 1 or current & 0xff == 0)


def _trap_prefix(data):
    if len(data) < _TRAP_MIN_ENTRIES * 8:
        return False
    ids = [_trap_id(data, i * 8) for i in range(_TRAP_MIN_ENTRIES)]
    return all(value is not None for value in ids) and all(
        _next_trap_id(a, b) for a, b in zip(ids, ids[1:]))


def _called_trap_table(rom, lo, hi, calls):
    """A directly called run of ordered trap stubs in an already byte-verified RAM range."""
    calls = set(calls)
    at = lo
    while at + _TRAP_MIN_ENTRIES * 8 <= hi:
        start = rom.find(_TRAP_JUMP, at, hi)
        if start < 0:
            break
        if start % 4 or (previous := _trap_id(rom, start)) is None:
            at = start + 1
            continue
        end = start + 8
        while end + 8 <= hi and (current := _trap_id(rom, end)) is not None and _next_trap_id(previous, current):
            previous = current
            end += 8
        if end - start >= _TRAP_MIN_ENTRIES * 8 and any(start <= call < end and (call - start) % 8 == 0 for call in calls):
            return start, end
        at = end
    return None


def trap_symbols(rom, ranges):
    """Name every entry of a whole verified table so the splitter retains uncalled entries too."""
    symbols = []
    for start, end, vram in ranges:
        if end > len(rom) or end - start < _TRAP_MIN_ENTRIES * 8 or (end - start) % 8:
            continue
        ids = [_trap_id(rom, at) for at in range(start, end, 8)]
        if any(value is None for value in ids) or not all(_next_trap_id(a, b) for a, b in zip(ids, ids[1:])):
            continue
        symbols.extend(f'func_{address:08X} = 0x{address:X}; // type:func size:0x8'
                       for address in range(vram, vram + end - start, 8))
    return symbols


def moved_banks(rom, banks, candidates, known, dumps, check=lambda: None):
    """Defer a bank when several independent bodies repeatedly appear at another coherent mapping.

    The bytes may be copies rather than executed code. This is evidence for review, never permission to
    change an existing address. Even if another body appears at the configured address, the same bank can be
    loaded in more than one place, so the unconfirmed entries still need review.
    """
    if len(dumps) < 2:
        return []
    evidence = defaultdict(Counter)
    candidate_banks = {bank for bank in banks for lo, hi, vram in candidates
                       if bank[0] <= lo < hi <= bank[1] and bank[2] + lo - bank[0] == vram}
    for lo, hi, vram in set(candidates) | set(known):
        check()
        owners = [(a, b, v) for a, b, v in banks if a <= lo < hi <= b and v + lo - a == vram]
        if len(owners) != 1 or lo < 0 or hi > len(rom) or hi - lo < 32:
            continue
        bank = owners[0]
        if bank not in candidate_banks:
            continue
        body = rom[lo:hi]
        expected = vram & 0x1fffffff
        if any(expected + len(body) <= len(ram) and ram[expected:expected + len(body)] == body for ram in dumps):
            continue
        locations = Counter()
        for ram in dumps:
            prefix = body[:64]
            offset = ram.find(prefix)
            if offset < 0 or ram.find(prefix, offset + 1) >= 0 or offset + len(body) > len(ram):
                continue
            if ram[offset:offset + len(body)] == body:
                locations[0x80000000 + offset] += 1
        for address, count in locations.items():
            if count >= 2 and address != vram:
                evidence[bank][address - lo] += 1
    return [(bank, shift, count) for bank, shifts in evidence.items()
            for shift, count in shifts.items() if count >= 2]


def inputs(vis: int = VIS) -> str:
    """Start, then A, every two seconds: past a title screen and into the menus."""
    return ",".join(f"{v}:START,{v + 8}:,{v + 60}:A,{v + 68}:" for v in range(120, vis, 120))


def _targets(rom: bytes, segments, image=None) -> set[int]:
    """Every call the segments' code makes to an address in RAM no segment covers."""
    known = [(v, v + e - s) for s, e, v in segments]
    out = set()
    for s, e, v in segments:
        data = (image or rom)[s:e]
        words = struct.unpack(f">{len(data) // 4}I", data[:len(data) // 4 * 4])
        out |= {t for t in jal_targets(words, v & 0xF0000000)
                if RAM[0] <= t < RAM[1] and not any(a <= t < b for a, b in known)}
    return out


def _region(rom: bytes, dump: bytes, target: int) -> tuple[int, int, int] | None:
    """(rom_start, rom_end, vram) of the code around `target` in RAM that the ROM holds as it is, or None."""
    o = target & 0x1FFFFFFF
    head = dump[o:o + CHUNK]
    if len(head) < CHUNK or not (any(is_frame(w) or w == JR_RA for w in struct.unpack(f">{CHUNK // 4}I", head))
                                  or _trap_prefix(head)):
        return None
    at = rom.find(head)
    if at < 0x1000 or rom.find(head, at + 1) >= 0:  # not in the ROM, or not once
        return None
    shift = o - at  # a RAM offset is its ROM offset plus this
    lo, hi = at, at + CHUNK
    while lo - 16 >= 0x1000 and lo - 16 + shift >= 0x400 and dump[lo - 16 + shift:lo + shift] == rom[lo - 16:lo]:
        lo -= 16
    while hi + 16 <= len(rom) and hi + 16 + shift <= len(dump) and dump[hi + shift:hi + 16 + shift] == rom[hi:hi + 16]:
        hi += 16
    # A run can begin or end between 16-byte boundaries. Keep complete words
    # at the edge so a frame is not lost to an unrelated changed word.
    while lo - 4 >= 0x1000 and lo - 4 + shift >= 0x400 and dump[lo - 4 + shift:lo + shift] == rom[lo - 4:lo]:
        lo -= 4
    while hi + 4 <= len(rom) and hi + 4 + shift <= len(dump) and dump[hi + shift:hi + 4 + shift] == rom[hi:hi + 4]:
        hi += 4
    return lo, hi, 0x80000000 + lo + shift  # the whole overlay as RAM holds it: find() cuts it to code


def _code(rom: bytes, lo: int, hi: int, calls=()) -> tuple[int, int] | None:
    """The code in ROM [lo, hi): from its first stack frame, or a call into it before that (a leaf function has no
    frame: a racing game's 0x8021F5F0 just before 0x8021F6A0's), to its last return (its delay slot too), or None."""
    if table := _called_trap_table(rom, lo, hi, calls):
        return table
    words = struct.unpack(f">{(hi - lo) // 4}I", rom[lo:hi - (hi - lo) % 4])
    frames = [i for i, w in enumerate(words) if is_frame(w)]
    returns = [i for i, w in enumerate(words) if w == JR_RA]
    if not frames or not returns or returns[-1] < frames[0]:
        return None
    start = min([lo + 4 * frames[0]] + [c for c in calls if lo <= c < lo + 4 * frames[0] and not c & 3])
    return start, lo + 4 * returns[-1] + 8


def find(rom_path: Path, rom: bytes, taken, found=(), *, log=print, stop=lambda: False, inspect_dumps=None,
         expansion_pak=False, vis=VIS, dumps_at=DUMPS) -> dict:
    """{"segments": [(rom_start, rom_end, vram)], "decoded": n, "moved": [...]} found by running the ROM: segments
    clear of `taken` and `found` (the split's and the static search's), each the ROM's own bytes at their address."""
    from . import emulator

    if emulator.ensure(log) is None:
        return {"segments": [], "decoded": 0, "moved": [], "skipped": "the reversing emulator isn't installed"}
    known = list(taken) + list(found)
    late_dumps = []
    with tempfile.TemporaryDirectory(prefix="decomp-loaded-code-") as tmp:
        try:
            if expansion_pak:
                run = emulator.run(Path(rom_path), vis, inputs=inputs(vis),
                                   dumps={v: Path(tmp) / f"{v}.bin" for v in dumps_at},
                                   timeout=300, stop=stop, expansion_pak=True)
                expansion = True
            else:
                run, expansion = emulator.run_for_observation(
                    Path(rom_path), vis, inputs=inputs(vis), dumps={v: Path(tmp) / f"{v}.bin" for v in dumps_at},
                    timeout=300, stop=stop, log=log)
        except emulator.EmulatorError as error:
            return {"segments": [], "decoded": 0, "moved": [], "skipped": str(error)}
        dumps = [p.read_bytes() for _, p in sorted(run.dumps.items()) if p.is_file()]
        from . import runtime_overlays
        check = getattr(log, 'check_cancel', lambda: None)
        if runtime_overlays.ambiguous(rom, known, check=check):
            log('Observing later code banks to distinguish overlapping runtime addresses.')
            try:
                late = emulator.run(Path(rom_path), 6000,
                                    dumps={v: Path(tmp) / f"late-{v}.bin" for v in (3000, 4500, 6000)},
                                    timeout=300, stop=stop, expansion_pak=expansion)
                late_dumps = [p.read_bytes() for _, p in sorted(late.dumps.items()) if p.is_file()]
            except emulator.EmulatorError as error:
                log(f'Later code-bank observation was unavailable: {error}')
    if inspect_dumps is not None:
        inspect_dumps(dumps)
    added: list[tuple[int, int, int]] = []
    moved, moved_ranges, decoded, seen = [], set(), set(), set()
    calls = _targets(rom, known)
    seeds = {v for _, _, v in found}  # inspect partial inferred ranges against the captured load
    todo = calls | seeds
    called = set(calls)  # every call into code no segment has, for where a segment starts
    while todo:
        target = todo.pop()
        if target in seen:
            continue
        seen.add(target)
        seed = target in seeds
        if not seed and any(v <= target < v + e - s for s, e, v in known + added):
            continue
        region, code = None, False
        for dump in dumps:
            o = target & 0x1FFFFFFF
            if o + 32 <= len(dump) and (any(is_frame(w) or w == JR_RA for w in struct.unpack(">8I", dump[o:o + 32]))
                                        or _trap_prefix(dump[o:o + CHUNK])):
                code = True
                region = _region(rom, dump, target)
                if region:
                    break
        if region is None:
            if code:
                decoded.add(target)  # code in RAM the ROM doesn't hold as it is: decoded as it loads
            continue
        s, e, v = region
        shift = v - s  # a ROM offset's load address is the offset plus this
        if seed and not any(v0 == target and v0-s0 == shift for s0, _, v0 in found):
            continue  # a different bank happened to occupy this inferred address
        overlapping = [(ks, ke, kv) for ks, ke, kv in known + added if ks < e and s < ke]
        for ks, ke, kv in overlapping:  # the split's own segment here: the run says where the game runs it
            if kv - ks != shift and not any(m["rom"] == max(s, ks) for m in moved):
                moved.append({"rom": max(s, ks), "split": kv + max(s, ks) - ks, "runs_at": max(s, ks) + shift})
        if any(kv - ks != shift for ks, ke, kv in overlapping):
            moved_ranges.add((s, e, v))
            continue
        # the region is a whole overlay: what the split has of it already comes out, the rest is added
        pieces, at = [], s
        for ks, ke, _ in sorted(overlapping):
            if ks > at:
                pieces.append((at, ks))
            at = max(at, ke)
        if at < e:
            pieces.append((at, e))
        for lo, hi in pieces:
            piece = _code(rom, lo, hi, [c - shift for c in called])
            if piece is None:
                continue
            ps, pe = piece
            if any(a < ps + shift + pe - ps and ps + shift < b for a, b in ((kv, kv + ke - ks) for ks, ke, kv in known + added)):
                continue
            added.append((ps, pe, ps + shift))
            new = _targets(rom, [(ps, pe, ps + shift)])
            called |= new
            todo |= new - seen
    added.sort()
    kib = sum(e - s for s, e, _ in added) // 1024
    if added:
        log(f"found {len(added)} more code segment{'s' if len(added) != 1 else ''} by running the game ({kib} KiB)")
    if decoded:
        log(f"{len(decoded)} functions the code calls are decoded as the game loads them: not in the ROM as they run")
    if moved:
        log(f"{len(moved)} places the split puts at another address than the game runs them: " + ", ".join(
            f"ROM {m['rom']:#x} at {m['split']:#x}, run at {m['runs_at']:#x}" for m in moved[:4]))
    from . import runtime_mio, runtime_mio_chunks, runtime_zlib
    programs = runtime_zlib.find(rom, dumps, decoded, known+added, check=check)
    from . import runtime_refpack
    occupied_programs = [(0, len(runtime_zlib._payload(rom, row)), row['vram']) for row in programs]
    # A previous decoder may have appended an analysis image to `rom`.
    # Runtime program sources must come from the original cartridge.
    programs += runtime_refpack.find(Path(rom_path).read_bytes(), dumps, decoded,
                                     known+added+occupied_programs, check=check)
    occupied_programs = [(0, len(runtime_zlib._payload(rom, row)), row['vram']) for row in programs]
    programs += runtime_mio_chunks.find(rom, dumps, decoded, known+added+occupied_programs, check=check)
    occupied_programs = [(0, len(runtime_zlib._payload(rom, row)), row['vram']) for row in programs]
    programs += runtime_mio.find(Path(rom_path).read_bytes(), dumps, decoded,
                                 known+added+occupied_programs, check=check)
    overlaps = runtime_overlays.regions(rom, dumps+late_dumps, known+added,
                                       check=getattr(log, 'check_cancel', lambda: None), moved=moved_ranges)
    if overlaps:
        log(f'Found {len(overlaps)} independently corroborated overlapping code bank(s) in runtime snapshots.')
    return {"segments": added, "overlap_segments": overlaps, "decoded": len(decoded), "moved": moved, "moved_ranges": sorted(moved_ranges),
            "decoded_programs": programs}
