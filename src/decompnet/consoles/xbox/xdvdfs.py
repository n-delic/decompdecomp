"""Read files out of an original Xbox disc image (XDVDFS), so a game can start from its disc as well as from its
program (default.xbe).

Xbox discs come in two dump layouts:

- an XISO (what extract-xiso and most homebrew tools make): the filesystem starts at the image's first byte;
- a full Redump dump (7.8 GB): a video partition first, the game's filesystem at byte 0x18300000.

Either way the volume descriptor sits 0x10000 bytes into the filesystem: `MICROSOFT*XBOX*MEDIA`, then the root
directory's sector and size. A directory is a binary tree of entries (left and right child as offsets in 4-byte
units, start sector, size, attributes, name), which this walks whole rather than searching by name, because the
order some mastering tools wrote doesn't follow the uppercase-name order the kernel's lookup expects.

Images are read through a file handle and only the sectors needed, never loaded whole: a Redump image is 7.8 GB.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ...forks.project import ForkError

SECTOR = 0x800
MAGIC = b"MICROSOFT*XBOX*MEDIA"
DESCRIPTOR = 32 * SECTOR  # the volume descriptor's place in the filesystem
LAYOUTS = {"xiso": 0, "redump": 0x18300000}  # where the filesystem starts in each kind of dump
DIRECTORY = 0x10  # the attribute bit of a directory entry
MAX_ENTRIES = 1 << 16  # a guard against a damaged tree that loops


@dataclass
class Entry:
    path: str  # inside the disc, with forward slashes: "media/intro.xmv"
    sector: int  # relative to the filesystem's start
    size: int
    directory: bool


def _open(disc: Path | bytes | BinaryIO) -> tuple[BinaryIO, bool]:
    """A binary handle on the image, and whether this opened it (and so must close it)."""
    if isinstance(disc, (bytes, bytearray, memoryview)):
        return io.BytesIO(disc), True
    if isinstance(disc, (str, Path)):
        return open(disc, "rb"), True
    return disc, False


def _read(f: BinaryIO, at: int, n: int) -> bytes:
    f.seek(at)
    return f.read(n)


def layout(disc: Path | bytes | BinaryIO) -> str | None:
    """"xiso" or "redump" when the image holds an Xbox filesystem in that layout, else None."""
    f, mine = _open(disc)
    try:
        for kind, base in LAYOUTS.items():
            if _read(f, base + DESCRIPTOR, len(MAGIC)) == MAGIC:
                return kind
        return None
    except OSError:
        return None
    finally:
        if mine:
            f.close()


def is_xbox_disc(disc: Path | bytes | BinaryIO) -> bool:
    """An original Xbox disc image, in either dump layout."""
    return layout(disc) is not None


def _walk(f: BinaryIO, base: int, sector: int, size: int, prefix: str, out: list[Entry], depth: int = 0) -> None:
    if not size or depth > 32 or len(out) > MAX_ENTRIES:
        return
    data = _read(f, base + sector * SECTOR, size)
    todo, seen = [0], set()
    while todo and len(out) <= MAX_ENTRIES:
        at = todo.pop() * 4
        if at in seen or at + 14 > len(data):
            continue
        seen.add(at)
        left, right, start, length, attrs, name_len = struct.unpack_from("<HHIIBB", data, at)
        if left == 0xFFFF:  # the padding that fills the rest of a directory's sector
            continue
        name = data[at + 14:at + 14 + name_len].decode("latin-1")
        path = f"{prefix}{name}"
        is_dir = bool(attrs & DIRECTORY)
        out.append(Entry(path, start, length, is_dir))
        if is_dir:
            _walk(f, base, start, length, path + "/", out, depth + 1)
        todo += [o for o in (left, right) if o]


def entries(disc: Path | bytes | BinaryIO) -> list[Entry]:
    """Every file and directory on the disc."""
    f, mine = _open(disc)
    try:
        kind = layout(f)
        if kind is None:
            raise ForkError("this isn't an original Xbox disc image: its filesystem (XDVDFS) wasn't found at the start "
                            "of the image (XISO) or at 0x18300000 (a full Redump dump)")
        base = LAYOUTS[kind]
        root, size = struct.unpack("<II", _read(f, base + DESCRIPTOR + 0x14, 8))
        out: list[Entry] = []
        _walk(f, base, root, size, "", out)
        return out
    finally:
        if mine:
            f.close()


def read_file(disc: Path | bytes | BinaryIO, inner: str) -> bytes:
    """A file's bytes, by its path inside the disc (case doesn't matter, as on the Xbox)."""
    want = inner.replace("\\", "/").strip("/").lower()
    f, mine = _open(disc)
    try:
        for e in entries(f):
            if e.path.lower() == want and not e.directory:
                return _read(f, LAYOUTS[layout(f)] + e.sector * SECTOR, e.size)
        raise ForkError(f"the disc image has no {inner}: an Xbox game disc starts its game from default.xbe")
    finally:
        if mine:
            f.close()
