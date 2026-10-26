"""PARAM.SFO: the facts a PSP disc keeps about its game (PSP_GAME/PARAM.SFO), as keys and values: TITLE, DISC_ID
(the product code, e.g. ULUS10041), DISC_VERSION, CATEGORY ("UG" for a UMD game) and the system version it needs.

The file: a header ("\\0PSF", a version, where the key table and the data table start, how many entries), then one
16-byte entry per key (where its name is in the key table, its format, its length, its room, where its value is in
the data table).
"""

from __future__ import annotations

import struct

from ...rom import RomError

MAGIC = b"\0PSF"
HEADER = struct.Struct("<4sIIII")  # magic, version, key table, data table, entries
ENTRY = struct.Struct("<HHIII")  # key's offset, format, length, room, value's offset
FORMAT_INTEGER = 0x0404  # a 32-bit number; the others (0x0004, 0x0204) are UTF-8 text
# a product code's first four letters -> the region its discs were sold in
REGIONS = {"ULUS": "North America", "UCUS": "North America", "ULES": "Europe", "UCES": "Europe",
           "ULJM": "Japan", "ULJS": "Japan", "UCJS": "Japan", "UCJM": "Japan", "NPJH": "Japan",
           "ULAS": "Asia", "UCAS": "Asia", "ULKS": "Korea", "UCKS": "Korea"}


def read(data: bytes) -> dict[str, str | int]:
    """Every key of a PARAM.SFO and its value (text or a number)."""
    if len(data) < HEADER.size or data[:4] != MAGIC:
        raise RomError("this disc's PSP_GAME/PARAM.SFO isn't one the PSP reads: the disc image may be damaged")
    _, _, keys_at, values_at, count = HEADER.unpack_from(data)
    out: dict[str, str | int] = {}
    for k in range(count):
        at = HEADER.size + ENTRY.size * k
        if at + ENTRY.size > len(data):
            break
        key_at, form, length, _, value_at = ENTRY.unpack_from(data, at)
        key = data[keys_at + key_at:data.find(b"\0", keys_at + key_at)].decode("ascii", "replace")
        raw = data[values_at + value_at:values_at + value_at + length]
        if form == FORMAT_INTEGER and len(raw) >= 4:
            out[key] = struct.unpack_from("<I", raw)[0]
        else:
            out[key] = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return out


def product_code(disc_id: str) -> str:
    """The product code as Sony prints it: ULUS10041 -> ULUS-10041."""
    disc_id = disc_id.strip().upper()
    return f"{disc_id[:4]}-{disc_id[4:]}" if len(disc_id) > 4 and disc_id[:4].isalpha() else disc_id


def region_of(code: str) -> str:
    return REGIONS.get(code[:4].upper(), "Unknown region")
