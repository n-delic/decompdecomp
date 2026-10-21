"""PlayStation 2 disc images: the file system, and the program SYSTEM.CNF names.

A PlayStation 2 game's code is one program on the disc (named in SYSTEM.CNF, e.g. SLUS_200.00). Discs are ISO 9660,
stored either as 2048-byte sectors (.iso) or raw 2352-byte sectors (.bin, Mode 1 or Mode 2 Form 1, as CD games
were). We read only the file system and the program, from the user's own image; nothing here touches copy
protection.
"""

from __future__ import annotations

import re
import struct

from ...rom import RomError


def region_of(serial: str) -> str:
    """Region from a Sony product code (SLUS, SCES, SLPM...)."""
    s = serial.upper()
    if s[:4] in ("SLUS", "SCUS", "PAPX"):
        return "North America"
    if s[:4] in ("SLES", "SCES", "SCED"):
        return "Europe"
    if s[:4] in ("SLPS", "SLPM", "SCPS", "SCPM", "SIPS"):
        return "Japan"
    if s[:4] in ("SLKA", "SCKA", "SCAJ"):
        return "Asia"
    return "Unknown region"


class _Disc:
    """An ISO 9660 image (CD or DVD), read from bytes or from an open file, so discs of several gigabytes never
    have to fit in memory."""

    def __init__(self, src):
        if isinstance(src, (bytes, bytearray, memoryview)):
            self._get = lambda at, n: bytes(src[at:at + n])
        else:
            def get(at: int, n: int) -> bytes:
                src.seek(at)
                return src.read(n)
            self._get = get
        # 2048-byte sectors (.iso), raw 2352-byte ones (.bin: Mode 2 or Mode 1), or a CHD's frames (2352 + 96 of subcode)
        for size, skip in ((2048, 0), (2352, 24), (2352, 16), (2448, 24), (2448, 16)):
            pvd = 16 * size + skip
            if self._get(pvd + 1, 5) == b"CD001":
                self.size, self.skip = size, skip
                return
        raise RomError("not a disc image this app can read (expected an ISO 9660 .bin, .iso or .chd)")

    def sector(self, lba: int) -> bytes:
        return self._get(lba * self.size + self.skip, 2048)

    def read(self, lba: int, length: int) -> bytes:
        if self.size == 2048:
            return self._get(lba * 2048, length)
        out = bytearray()
        while len(out) < length:
            out += self.sector(lba)
            lba += 1
        return bytes(out[:length])

    def entries(self, lba: int, length: int):
        raw = self.read(lba, length)
        i = 0
        while i < len(raw):
            n = raw[i]
            if n == 0:  # records don't cross sector boundaries; skip the padding
                i = (i // 2048 + 1) * 2048
                continue
            rec = raw[i:i + n]
            name = rec[33:33 + rec[32]].decode("ascii", "replace").split(";", 1)[0]
            if name not in ("\x00", "\x01"):
                yield name.upper(), struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0], bool(rec[25] & 2)
            i += n

    def find(self, path: str) -> bytes | None:
        pvd = self.sector(16)
        lba, length = struct.unpack_from("<I", pvd, 156 + 2)[0], struct.unpack_from("<I", pvd, 156 + 10)[0]
        parts = [p for p in re.split(r"[\\/]", path.upper()) if p]
        for k, part in enumerate(parts):
            hit = next(((l, s, d) for name, l, s, d in self.entries(lba, length) if name == part), None)
            if hit is None:
                return None
            lba, length, is_dir = hit
            if k == len(parts) - 1:
                return None if is_dir else self.read(lba, length)
        return None


def from_disc(src) -> tuple[bytes, str]:
    """(program bytes, its file name) from a PlayStation 2 disc image: SYSTEM.CNF names the program
    (`BOOT2 = cdrom0:\\SLUS_200.00;1`). A PlayStation 1 disc says `BOOT = cdrom:\\...` instead (consoles/ps1/)."""
    disc = _Disc(src)
    cnf = disc.find("SYSTEM.CNF")
    m = re.search(rb"BOOT2\s*=\s*cdrom0?:?\\*([^\s;]+)", cnf or b"", re.I)
    if m is None:
        raise RomError("this disc isn't a PlayStation 2 game: its SYSTEM.CNF names no BOOT2 program")
    boot = m.group(1).decode("ascii", "replace")
    exe = disc.find(boot)
    if exe is None:
        raise RomError(f"the disc names {boot} as its program, but it isn't on the disc")
    return exe, boot.replace("\\", "/").rsplit("/", 1)[-1]


def is_disc(data) -> bool:
    try:
        _Disc(data)
        return True
    except RomError:
        return False
