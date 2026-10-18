#!/usr/bin/env python3
"""Give what src/ compiles to the original's symbol names, so C++ pairs with the game's functions in objdiff.

    python tools/names.py config/<version>/functions.json OBJECT

The game's functions and data are known by address: func_0041B470, D_0066DDA4. Written in C++, as a free function or
a class's method (`void Town::func_0041B470(int)`), MSVC mangles the name (`?func_0041B470@Town@@QAEXH@Z`); this
renames every symbol whose name holds func_<address> or D_<address> to the name the original's object gives it
(`@func_0041B470@8`, `_D_0066DDA4`), in the object itself. C names are left as they are. Standard library only.
Made by decompdecomp.
"""

import json
import re
import struct
import sys
from pathlib import Path

REF = re.compile(rb"(func|D)_([0-9A-Fa-f]{8})")


def wanted(info: dict) -> dict[bytes, bytes]:
    """func_XXXXXXXX / D_XXXXXXXX (upper case address) -> the original's symbol."""
    out = {}
    for functions in info["units"].values():
        for fn in functions:
            out[f"func_{fn['addr']:08X}".encode()] = fn["name"].encode()
            for _, _, name, _ in fn["relocs"]:
                if name and name.startswith("_D_"):
                    out[name[1:].encode()] = name.encode()
    return out


def rename(data: bytes, names: dict[bytes, bytes]) -> bytes:
    """The COFF object with its symbols renamed (new names go at the end of the string table)."""
    sym_at, count = struct.unpack_from("<II", data, 8)
    strings_at = sym_at + 18 * count
    strings = bytearray(data[strings_at:])
    table = bytearray(data[sym_at:strings_at])
    i = 0
    while i < count:
        at = 18 * i
        raw = table[at:at + 8]
        if raw[:4] == b"\0\0\0\0":
            off = struct.unpack_from("<I", raw, 4)[0]
            name = bytes(strings[off:strings.index(b"\0", off)])
        else:
            name = bytes(raw).rstrip(b"\0")
        m = REF.search(name)
        if m and not name.startswith(b".") and name != names.get(m.group(1) + b"_" + m.group(2).upper()):
            new = names.get(m.group(1) + b"_" + m.group(2).upper())
            if new is None and m.group(1) == b"D":
                new = b"_" + m.group(1) + b"_" + m.group(2).upper()  # data the original's code doesn't name
            if new is not None:
                if len(new) <= 8:
                    table[at:at + 8] = new.ljust(8, b"\0")
                else:
                    table[at:at + 8] = struct.pack("<II", 0, len(strings))
                    strings += new + b"\0"
        i += 1 + table[at + 17]  # its auxiliary records
    struct.pack_into("<I", strings, 0, len(strings))
    return data[:sym_at] + bytes(table) + bytes(strings)


def main() -> None:
    config, obj = Path(sys.argv[1]), Path(sys.argv[2])
    names = wanted(json.loads(config.read_text(encoding="utf-8")))
    data = obj.read_bytes()
    obj.write_bytes(rename(data, names))


if __name__ == "__main__":
    main()
