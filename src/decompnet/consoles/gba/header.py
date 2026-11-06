"""A Game Boy Advance cartridge's header: whether a file is one, and what its header says.

The console's boot check reads three things from the header, and so does this: an ARM branch at 0 to the start-up
code, the fixed byte 0x96 at 0xB2, and the complement checksum at 0xBD over the title, game code, maker code and
version (0xA0-0xBC). The 156-byte logo at 0x04 is Nintendo's artwork, so nothing here reads or keeps it: the demo's
is blank, and a match needs only the user's own copy.
"""

from __future__ import annotations

import hashlib
import struct

from ...rom import RomError

BASE = 0x08000000  # the cartridge's first byte in the CPU's address space
HEADER_BYTES = 0xC0  # through the checksum and the two reserved bytes after it
LOGO_START, LOGO_END = 0x04, 0xA0  # Nintendo's logo: never copied, never compared
TITLE_START, TITLE_END = 0xA0, 0xAC  # 12 bytes, padded with zeros
CODE_START, CODE_END = 0xAC, 0xB0  # four ASCII characters: the game code, its last one the region
MAKER_START, MAKER_END = 0xB0, 0xB2
FIXED_START = 0xB2  # 0x96: the console's boot check wants it there
VERSION = 0xBC
CHECKSUM = 0xBD
CHECKSUM_SUM_START, CHECKSUM_SUM_END = TITLE_START, CHECKSUM  # everything the checksum covers
CHECKSUM_CONSTANT = 0x19
ENTRY_BRANCH = 0xEA  # the top byte of the ARM `b` at 0 (always executed, `b`)
REGIONS = {
    "A": "All regions", "C": "China", "D": "Germany", "E": "North America", "F": "France", "I": "Italy",
    "J": "Japan", "K": "Korea", "P": "Europe", "S": "Spain", "X": "Europe", "Y": "Europe",
}


def branch_target(data: bytes) -> int:
    """Where the ARM branch at 0 goes: the start-up code (0x080000C0 in the demo)."""
    word, = struct.unpack_from("<I", data, 0)
    offset = word & 0xFFFFFF
    if offset & 0x800000:  # the branch's signed 24-bit word offset
        offset -= 0x1000000
    return (BASE + 8 + (offset << 2)) & 0xFFFFFFFF


def checksum(data: bytes) -> int:
    """The header's complement checksum: what 0xBD holds for the title and version bytes at 0xA0-0xBC."""
    return (-(sum(data[CHECKSUM_SUM_START:CHECKSUM_SUM_END]) + CHECKSUM_CONSTANT)) & 0xFF


def is_gba(data: bytes) -> bool:
    """Whether these first bytes are a GBA cartridge header that checks out. Strict: the first console that says
    yes imports the file."""
    if len(data) < HEADER_BYTES or data[FIXED_START] != 0x96:
        return False
    if data[3] != ENTRY_BRANCH:  # an ARM `b` to the start-up code, at the cartridge's first word
        return False
    return checksum(data) == data[CHECKSUM]


def _text(raw: bytes) -> str:
    try:
        return raw.split(b"\0", 1)[0].decode("ascii").strip()
    except UnicodeDecodeError:
        return raw.split(b"\0", 1)[0].decode("latin-1").strip()


def read_info(data: bytes) -> dict:
    """What the cartridge's header says, for the import's log and the project's config."""
    if not is_gba(data):
        raise RomError("this isn't a Game Boy Advance ROM: its header's branch, fixed 0x96 byte or checksum is wrong")
    code = _text(data[CODE_START:CODE_END])
    title = _text(data[TITLE_START:TITLE_END]) or code or "Game Boy Advance game"
    return {
        "title": title,
        "game_code": code,
        "region": REGIONS.get(code[3:4], "Unknown region") if len(code) == 4 else "Unknown region",
        "version": data[VERSION],
        "size": len(data),
        "sha1": hashlib.sha1(data).hexdigest(),
        "entrypoint": BASE,
        "startup": branch_target(data),
        "platform": "gba",
        "byte_order": "gba",
        "compiler_guess": "AGBCC",
        "gp": 0,
    }
