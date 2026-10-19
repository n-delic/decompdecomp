"""The PlayStation's program (a PS-X EXE: a 2 KiB header, then the code and data it loads) and the disc it comes on.

A PS1 game's code is one program on its CD, named in SYSTEM.CNF (`BOOT = cdrom:\\SLUS_000.01;1`), or PSX.EXE on
discs without one. The disc is read with the PS2's ISO 9660 and CHD readers (consoles/ps2/disc.py, chd.py): only the
file system and the program, from the user's own image; nothing here touches copy protection.
"""

from __future__ import annotations

import hashlib
import re
import struct
from contextlib import contextmanager
from pathlib import Path

from ...rom import RomError
from ..ps2 import chd, disc

MAGIC = b"PS-X EXE"
HEADER = 0x800  # the header; the program's bytes follow it
HEADER_FIELDS = 0x10  # pc0, gp0, t_addr, t_size: where it starts, its $gp, where it loads and how much
MARKER = slice(0x4C, HEADER)  # "Sony Computer Entertainment Inc. for North America area"
RAM = (0x80000000, 0x80200000)  # its 2 MiB, as the CPU's cached segment sees it
NO_CNF_PROGRAM = "PSX.EXE"  # what the BIOS boots when a disc has no SYSTEM.CNF
MARKER_REGIONS = {"North America": "North America", "Japan": "Japan", "Europe": "Europe"}


def is_exe(head: bytes) -> bool:
    return head[:len(MAGIC)] == MAGIC


def read_exe(data: bytes, boot_name: str = "") -> tuple[bytes, dict]:
    """The program as it loads (its header and the bytes the header says), with what the header and name say."""
    if not is_exe(data):
        raise RomError("this isn't a PlayStation program (a PS-X EXE, e.g. SLUS_000.01)")
    pc, gp, load, size = struct.unpack_from("<4I", data, HEADER_FIELDS)
    if not (RAM[0] <= load < RAM[1] and RAM[0] <= pc < RAM[1]) or size == 0 or HEADER + size > len(data):
        raise RomError("the program's header doesn't make sense (where it loads, or how big it is)")
    marker = data[MARKER].split(b"\0", 1)[0].decode("ascii", "replace")
    code = ""
    m = re.match(r"^([A-Z]{4})[_-](\d{3})\.?(\d{2})", boot_name.upper())
    if m:
        code = f"{m.group(1)}-{m.group(2)}{m.group(3)}"
    region = disc.region_of(code) if code else next((v for k, v in MARKER_REGIONS.items() if k in marker),
                                                      "Unknown region")
    exe = data[:HEADER + size]
    return exe, {
        "title": code or boot_name or "PlayStation game", "game_code": code, "region": region, "version": 0,
        "entrypoint": pc, "gp": gp, "load": load, "crc1": 0, "crc2": 0, "size": len(exe),
        "sha1": hashlib.sha1(exe).hexdigest(), "byte_order": "exe", "compiler_guess": "PSYQ", "platform": "ps",
        "boot": boot_name,
    }


def boot_name(image) -> str | None:
    """The program a PS1 disc boots (`image`: a disc._Disc), or None when it's another console's disc (a PS2's says
    BOOT2)."""
    cnf = image.find("SYSTEM.CNF")
    if cnf is None:
        return NO_CNF_PROGRAM if image.find(NO_CNF_PROGRAM) is not None else None
    if re.search(rb"BOOT2\s*=", cnf, re.I):
        return None
    m = re.search(rb"BOOT\s*=\s*cdrom:?\\*([^\s;]+)", cnf, re.I)
    return m.group(1).decode("ascii", "replace") if m else None


@contextmanager
def opened(file):
    """A disc image to read in place: bytes as they are, a CHD a hunk at a time, any other file from disk."""
    if not isinstance(file, (str, Path)):
        yield file
        return
    with open(file, "rb") as f:
        head = f.read(8)
        if not chd.is_chd(head):
            yield f
            return
    with chd.Chd(file) as image:
        yield image


def is_ps1_disc(file) -> bool:
    try:
        with opened(file) as src:
            return disc.is_disc(src) and boot_name(disc._Disc(src)) is not None
    except (RomError, OSError, struct.error, IndexError):
        return False
