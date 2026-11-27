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
from pathlib import Path

from ...arch.mips import JR_RA, is_frame, jal_targets

VIS = 1200  # 20 seconds of the game (PAL 24): about two seconds to run
DUMPS = (60, 300, 600, 900, 1200)
CHUNK = 128  # the RAM a function starts with, found once in the ROM, ties RAM to ROM
RAM = (0x80000400, 0x80800000)


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
    if len(head) < CHUNK or not any(is_frame(w) or w == JR_RA for w in struct.unpack(f">{CHUNK // 4}I", head)):
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
    return lo, hi, 0x80000000 + lo + shift  # the whole overlay as RAM holds it: find() cuts it to code


def _code(rom: bytes, lo: int, hi: int, calls=()) -> tuple[int, int] | None:
    """The code in ROM [lo, hi): from its first stack frame, or a call into it before that (a leaf function has no
    frame: a racing game's 0x8021F5F0 just before 0x8021F6A0's), to its last return (its delay slot too), or None."""
    words = struct.unpack(f">{(hi - lo) // 4}I", rom[lo:hi - (hi - lo) % 4])
    frames = [i for i, w in enumerate(words) if is_frame(w)]
    returns = [i for i, w in enumerate(words) if w == JR_RA]
    if not frames or not returns or returns[-1] < frames[0]:
        return None
    start = min([lo + 4 * frames[0]] + [c for c in calls if lo <= c < lo + 4 * frames[0] and not c & 3])
    return start, lo + 4 * returns[-1] + 8


def find(rom_path: Path, rom: bytes, taken, found=(), *, log=print, stop=lambda: False) -> dict:
    """{"segments": [(rom_start, rom_end, vram)], "decoded": n, "moved": [...]} found by running the ROM: segments
    clear of `taken` and `found` (the split's and the static search's), each the ROM's own bytes at their address."""
    from . import emulator

    if emulator.ensure(log) is None:
        return {"segments": [], "decoded": 0, "moved": [], "skipped": "the reversing emulator isn't installed"}
    known = list(taken) + list(found)
    late_dumps = []
    with tempfile.TemporaryDirectory(prefix="decomp-loaded-code-") as tmp:
        try:
            run, expansion = emulator.run_for_observation(
                Path(rom_path), VIS, inputs=inputs(), dumps={v: Path(tmp) / f"{v}.bin" for v in DUMPS},
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
    added: list[tuple[int, int, int]] = []
    moved, moved_ranges, decoded, seen = [], set(), set(), set()
    todo = _targets(rom, known)
    called = set(todo)  # every call into code no segment has, for where a segment starts
    while todo:
        target = todo.pop()
        if target in seen:
            continue
        seen.add(target)
        if any(v <= target < v + e - s for s, e, v in known + added):
            continue
        region, code = None, False
        for dump in dumps:
            o = target & 0x1FFFFFFF
            if o + 32 <= len(dump) and any(is_frame(w) or w == JR_RA for w in struct.unpack(">8I", dump[o:o + 32])):
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
    from . import runtime_zlib
    programs = runtime_zlib.find(rom, dumps, decoded, known+added, check=check)
    from . import runtime_refpack
    occupied_programs = [(0, len(runtime_zlib._payload(rom, row)), row['vram']) for row in programs]
    programs += runtime_refpack.find(rom, dumps, decoded, known+added+occupied_programs, check=check)
    overlaps = runtime_overlays.regions(rom, late_dumps, known+added, check=getattr(log, 'check_cancel', lambda: None))
    if overlaps:
        log(f'Found {len(overlaps)} independently corroborated overlapping code bank(s) in later runtime snapshots.')
    return {"segments": added, "overlap_segments": overlaps, "decoded": len(decoded), "moved": moved, "moved_ranges": sorted(moved_ranges),
            "decoded_programs": programs}
