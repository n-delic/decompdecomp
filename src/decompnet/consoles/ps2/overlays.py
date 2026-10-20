"""PlayStation overlays: code a game keeps in files on its disc, besides the main executable (the PS1 uses this
too, with its own RAM).

Many PlayStation 1 and 2 games load part of their code from other files when they need it (a battle system, a level's
logic, a minigame): overlays. The community's projects list them by hand with the address each is loaded at, found
in the code that loads them or with an emulator. Here the code says it: every file on the disc is checked for MIPS
code, and its load address is the one that makes its own calls (jal: absolute addresses) land on its own functions'
starts (arch/mips.py's place, which finds the N64's segments too).

- A PS2 overlay that is an ELF program says where it goes: its loadable segments are taken as they are. IOP modules
  (.IRX: the I/O processor's code, relocatable) are left out.
- Several overlays may load at the same address (only one is in memory at a time): each is a segment of its own, and
  function names that repeat get the overlay's name in front (split.py).
- An archive (one big file of a game's areas, each with its code, models and sounds) holds its overlays inside:
  stretches of code, each placed by its calls the same way, and those that agree where the file would be loaded are
  one overlay (embedded). On a PS1 game, 92 areas' code in one 68 MB file, the program itself 630 KB of it.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from ...arch import mips
from ...arch.mips import JR_RA, is_frame

MAX_FILE = 16 << 20
MAX_ARCHIVE = 256 << 20  # files read whole to look for overlays inside (archives of a game's areas)
CHUNK = 16 << 20  # a big file's returns are counted this much at a time: one without code is never read whole
ARCHIVE_LEAST = 64  # returns an archive has at least, when it holds code
CLUSTER_GAP = 0x1000  # code has a return or a stack frame every few hundred bytes: further apart, another stretch
CLUSTER_MARKS = 8  # fewer in a stretch: data words that happen to read as code (mips.place wants 8 of each anyway)
# little-endian, at a word's start: jr ra, and addiu sp, sp, -n (a stack frame)
_MARK = re.compile(rb"\x08\x00\xe0\x03|[\x00-\xff][\x80-\xff]\xbd\x27", re.S)
PEEK = 64 << 10  # how much of a file is read first: code shows in its first 64 KiB
JR_RA_LE = struct.pack("<I", JR_RA)
RAM = (0x00080000, 0x02000000)  # where the EE's programs load: above the kernel, within its 32 MiB


def _swap(data: bytes) -> bytes:
    """Little-endian words as big-endian ones, for mips.place (offsets and addresses stay the same)."""
    n = len(data) // 4
    return struct.pack(f">{n}I", *struct.unpack(f"<{n}I", data[:n * 4]))


def _code_like(data: bytes, least: int = 16) -> bool:
    if data.count(JR_RA_LE) < least:  # quick: most files (graphics, sound, levels) stop here
        return False
    words = struct.unpack(f"<{len(data) // 4}I", data[:len(data) // 4 * 4])
    return sum(w == JR_RA for w in words) >= least and sum(is_frame(w) for w in words) >= least


def _walk(disc, lba: int, length: int, path: str = ""):
    for name, l, size, is_dir in disc.entries(lba, length):
        if is_dir:
            if name not in (".", ".."):
                yield from _walk(disc, l, size, f"{path}{name}/")
        else:
            yield f"{path}{name}", l, size


def disc_files(disc) -> list[tuple[str, int, int]]:
    """(path, lba, size) of every file on an ISO 9660 disc."""
    pvd = disc.sector(16)
    root_lba, root_len = struct.unpack_from("<I", pvd, 158)[0], struct.unpack_from("<I", pvd, 166)[0]
    return list(_walk(disc, root_lba, root_len))


def elf_segments(data: bytes) -> list[tuple[int, bytes]] | None:
    """(address, bytes) of an EE ELF program's loadable code; None if it isn't one (an IOP module is left out)."""
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        return None
    e_type, e_machine = struct.unpack_from("<HH", data, 16)
    if e_machine != 8 or e_type != 2:  # MIPS, executable (an IRX is 0xFF80: relocatable, for the IOP)
        return None
    phoff, = struct.unpack_from("<I", data, 28)
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    out = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags, _ = struct.unpack_from("<8I", data, phoff + i * phentsize)
        if p_type == 1 and p_filesz and p_flags & 1:  # PT_LOAD, executable
            out.append((p_vaddr, data[p_offset:p_offset + p_filesz]))
    return out


def place(data: bytes, known: list[tuple[int, int]], ram: tuple[int, int] = RAM) -> tuple[int, int] | None:
    """(the address a raw overlay file is loaded at, where in it its code starts), or None if it can't be told."""
    swapped = _swap(data)
    seg = mips.place(swapped, 0, len(swapped), 0, [], known, regions=(ram,))
    if seg is None:
        return None
    start, _, vram, _ = seg
    base = vram - start
    lo, hi = ram
    if not lo <= base < hi or base % 4:
        return None
    if any(a < base + len(data) and base < b for a, b in known):  # over the resident program: not where it goes
        return None
    return base, start


def embedded(data: bytes, known: list[tuple[int, int]], ram: tuple[int, int] = RAM) -> list[tuple[int, int, int]]:
    """(start, end, load address) in an archive file of the overlays it holds: its stretches of code (returns and
    stack frames close together), each placed by its own calls; neighbours that agree where the file's first byte
    would be are one overlay, with what lies between them (its jump tables, strings). The same code twice is kept
    once. shortcut: an overlay ends at its last function; its data after that stays out (its code refers to it by
    address), until a game needs its jump tables there."""
    marks = [m.start() for m in _MARK.finditer(data) if m.start() % 4 == 0]
    stretches, cur = [], []
    for at in marks:
        if cur and at - cur[-1] > CLUSTER_GAP:
            stretches.append(cur)
            cur = []
        cur.append(at)
    stretches = [(s[0], s[-1] + 8) for s in stretches + [cur] if len(s) >= CLUSTER_MARKS]
    runs: list[list[int]] = []  # [start, end, where the file's byte 0 would be]
    floor = 0
    for lo, hi in stretches:
        back = max(floor, lo - 2 * mips.SCAN_BLOCK)  # (place looks this far back for the first function)
        seg = mips.place(_swap(data[back:hi]), lo - back, hi - back, 0, [], known, regions=(ram,))
        if seg is None:
            continue
        start, end, vram, _ = seg
        start, end = back + start, back + end
        floor = end
        if runs and runs[-1][2] == vram - start:
            runs[-1][1] = end
        else:
            runs.append([start, end, vram - start])
    out, seen = [], set()
    for start, end, file_base in runs:
        vram = file_base + start
        key = (vram, data[start:end])
        if key in seen or any(a < vram + end - start and vram < b for a, b in known):
            continue
        seen.add(key)
        out.append((start, end, vram))
    return out


def find(disc, boot: str, main: tuple[int, int], ram: tuple[int, int] = RAM) -> list[tuple[str, bytes, int, int]]:
    """(name, bytes, load address, where its code starts) of the overlays on a PlayStation disc. `main` is the
    resident program's address range, `ram` where the console's programs load."""
    out = []
    names: set[str] = set()
    boot_name = boot.upper().replace("\\", "/").rsplit("/", 1)[-1]
    for path, lba, size in disc_files(disc):
        base_name = path.rsplit("/", 1)[-1]
        if base_name == boot_name or base_name == "SYSTEM.CNF" or not 0x400 <= size <= MAX_ARCHIVE:
            continue
        name = re.sub(r"[^A-Za-z0-9_]", "_", Path(base_name).stem).strip("_").lower() or "overlay"
        while name in names:
            name += "_"
        if size > MAX_FILE or size > PEEK and not _code_like(disc.read(lba, PEEK), 4):  # no code where it starts
            returns = sum(disc.read(lba + at // 2048, min(CHUNK, size - at)).count(JR_RA_LE) for at in range(0, size, CHUNK))
            if returns >= ARCHIVE_LEAST:  # an archive with code inside
                data = disc.read(lba, size)
                for start, end, vram in embedded(data, [main], ram):
                    out.append((f"{name}_{start:x}", data[start:end], vram, 0))
                names.add(name)
            continue
        data = disc.read(lba, size)
        if (segs := elf_segments(data)) is not None:
            for k, (vaddr, code) in enumerate(segs):
                if _code_like(code):
                    out.append((f"{name}{k or ''}", code, vaddr, 0))
                    names.add(f"{name}{k or ''}")
            continue
        if not _code_like(data):
            continue
        placed = place(data, [main], ram)
        if placed is not None:
            out.append((name, data, *placed))
            names.add(name)
    return out


def add_to_config(config: Path, workdir: Path, image: str, found: list[tuple[str, bytes, int, int]], log=print) -> str:
    """The overlays `find` found, each appended to a working copy of the program's image, as a code segment of its
    own at the address it's loaded at, in splat's config. Returns the image splat reads."""
    import hashlib

    import yaml

    data = bytearray((workdir / image).read_bytes())
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    segs = [s for s in cfg["segments"] if not isinstance(s, list)]
    for name, code, vram, code_at in found:
        data += bytes(-len(data) % 0x10 + 0x10)
        start = len(data)
        data += code
        subs = [[start, "data", f"ovl_{name}_header"]] if code_at else []  # what comes before its code: a header
        segs.append({"name": f"ovl_{name}", "type": "code", "start": start, "vram": vram,
                     "subsegments": subs + [[start + code_at, "c", f"ovl_{name}"]]})
    data += bytes(-len(data) % 0x10)
    segs.append([len(data)])
    out = f"{Path(image).stem}.overlays{Path(image).suffix}"
    (workdir / out).write_bytes(data)
    cfg["segments"] = segs
    cfg.setdefault("options", {})["target_path"] = out
    cfg["sha1"] = hashlib.sha1(bytes(data)).hexdigest()
    config.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None), encoding="utf-8")
    log(f"added {len(found)} overlay{'s' if len(found) != 1 else ''} ({sum(len(o[1]) for o in found) // 1024} KiB of code)")
    return out
