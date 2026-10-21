"""The PlayStation's program (a PS-X EXE: a 2 KiB header, then the code and data it loads) and the disc it comes on.

A PS1 game's code is one program on its CD, named in SYSTEM.CNF (`BOOT = cdrom:\\SLUS_000.01;1`), or PSX.EXE on
discs without one; or the program that one loads, when it's a loader (loaded_program). The disc is read with the
PS2's ISO 9660 and CHD readers (consoles/ps2/disc.py, chd.py): only the file system and the program, from the user's
own image; nothing here touches copy protection.
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


# a path a program names on its disc (`cdrom:\\GAME\\MAIN.EXE;1`)
LOADS = re.compile(rb"cdrom0?:\\*((?:[A-Za-z0-9_]+\\)*[A-Za-z0-9_]+\.EXE)", re.I)


def loaded_program(image, boot: bytes) -> tuple[str, bytes] | None:
    """(its path, its bytes) of the program the boot program loads, when that's a loader: a game whose code is
    another PS-X EXE on the disc, which the boot program names (one that shows a logo, then loads the game). The
    biggest of those it names; None when it names none."""
    found = [(path, raw) for path in {m.group(1).decode("ascii") for m in LOADS.finditer(boot)}
             if (raw := image.find(path)) is not None and is_exe(raw) and raw != boot]
    return max(found, key=lambda f: len(f[1]), default=None)


JR_RA, JR_T2 = 0x03E00008, 0x01400008  # a function's return; a BIOS call stub's jump (li t2, 0xA0; jr t2; li t1, n)
FRAME, FRAME_MASK = 0x27BD8000, 0xFFFF8000  # addiu sp, sp, -n: a function making its stack frame
CLUSTER_GAP = 0x1000  # code has a return or a frame every few hundred bytes at most
CLUSTER_MARKS = 4  # fewer in a cluster: words of data that happen to read as one


def text_range(program: bytes) -> tuple[int, int] | None:
    """(start, end) in the program of its code, from the code's own marks: from the first of the clusters of
    returns, frames and BIOS stubs to the last (PSY-Q links .text as one stretch). None when there are none."""
    marks = [at for at in range(HEADER, len(program) - 3, 4)
             if (w := int.from_bytes(program[at:at + 4], "little")) in (JR_RA, JR_T2) or w & FRAME_MASK == FRAME]
    clusters, cur = [], []
    for at in marks:
        if cur and at - cur[-1] > CLUSTER_GAP:
            clusters.append(cur)
            cur = []
        cur.append(at)
    clusters = [c for c in clusters + [cur] if len(c) >= CLUSTER_MARKS]
    if not clusters:
        return None
    # shortcut: a function without a frame before the first frame is left out (data to splat); none seen yet
    return clusters[0][0], min(clusters[-1][-1] + 8, len(program))  # (the last mark's delay slot)


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
