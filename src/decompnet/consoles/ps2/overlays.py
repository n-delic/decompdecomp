"""PlayStation 2 overlays: code a game keeps in files on its disc, besides the main executable.

Many PlayStation 2 games load part of their code from other files when they need it (a battle system, a level's
logic, a minigame): overlays. The community's projects list them by hand with the address each is loaded at, found
in the code that loads them or with an emulator. Here the code says it: every file on the disc is checked for MIPS
code, and its load address is the one that makes its own calls (jal: absolute addresses) land on its own functions'
starts (arch/mips.py's place, which finds the N64's segments too).

- A PS2 overlay that is an ELF program says where it goes: its loadable segments are taken as they are. IOP modules
  (.IRX: the I/O processor's code, relocatable) are left out.
- Several overlays may load at the same address (only one is in memory at a time): each is a segment of its own, and
  function names that repeat get the overlay's name in front (split.py).
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from ...arch import mips
from ...arch.mips import JR_RA, is_frame

MAX_FILE = 16 << 20
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


def place(data: bytes, known: list[tuple[int, int]]) -> tuple[int, int] | None:
    """(the address a raw overlay file is loaded at, where in it its code starts), or None if it can't be told."""
    swapped = _swap(data)
    seg = mips.place(swapped, 0, len(swapped), 0, [], known, regions=(RAM,))
    if seg is None:
        return None
    start, _, vram, _ = seg
    base = vram - start
    lo, hi = RAM
    if not lo <= base < hi or base % 4:
        return None
    if any(a < base + len(data) and base < b for a, b in known):  # over the resident program: not where it goes
        return None
    return base, start


def find(disc, boot: str, main: tuple[int, int]) -> list[tuple[str, bytes, int, int]]:
    """(name, bytes, load address, where its code starts) of the overlays on a PlayStation 2 disc. `main` is
    the resident program's address range."""
    out = []
    names: set[str] = set()
    boot_name = boot.upper().replace("\\", "/").rsplit("/", 1)[-1]
    for path, lba, size in disc_files(disc):
        base_name = path.rsplit("/", 1)[-1]
        if base_name == boot_name or base_name == "SYSTEM.CNF" or not 0x400 <= size <= MAX_FILE:
            continue
        if size > PEEK and not _code_like(disc.read(lba, PEEK), 4):  # a big file with no code where it starts
            continue
        data = disc.read(lba, size)
        name = re.sub(r"[^A-Za-z0-9_]", "_", Path(base_name).stem).strip("_").lower() or "overlay"
        while name in names:
            name += "_"
        if (segs := elf_segments(data)) is not None:
            for k, (vaddr, code) in enumerate(segs):
                if _code_like(code):
                    out.append((f"{name}{k or ''}", code, vaddr, 0))
                    names.add(f"{name}{k or ''}")
            continue
        if not _code_like(data):
            continue
        placed = place(data, [main])
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
