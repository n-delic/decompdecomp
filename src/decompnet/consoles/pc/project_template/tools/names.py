#!/usr/bin/env python3
"""Give what src/ compiles to the original's symbol names, so C++ pairs with the game's functions in objdiff.

    python tools/names.py config/<version>/functions.json OBJECT

The game's functions and data are known by address: func_0041B470, D_0066DDA4. Written in C++, as a free function or
a class's method (`void Town::func_0041B470(int)`), MSVC mangles the name (`?func_0041B470@Town@@QAEXH@Z`); this
renames every symbol whose name holds func_<address> or D_<address> to the name the original's object gives it
(`@func_0041B470@8`, `_D_0066DDA4`), in the object itself. A real name the project gave one (functions.json's
"names", kept by decompdecomp's rename_symbol: func_0041B470 -> UpdateTown) counts as its address too, written in C
or C++. Other names are left as they are. Standard library only. Made by decompdecomp.
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


def real_names(info: dict) -> dict[bytes, bytes]:
    """A real name the project gave (functions.json's "names": func_0041B470 -> UpdateTown) -> its address's key."""
    return {real.encode(): key.encode() for key, real in (info.get("names") or {}).items()}


def address_of(name: bytes, real: dict[bytes, bytes]) -> bytes | None:
    """func_XXXXXXXX / D_XXXXXXXX (upper case address) a symbol stands for: by the address in its name, or by a real
    name, as C (`_UpdateTown`, `_UpdateTown@8`, `@UpdateTown@8`) or C++ (`?UpdateTown@@YAXH@Z`, a method's
    `?UpdateTown@Town@@QAEXH@Z`) spells it."""
    if name.startswith(b"."):
        return None
    if m := REF.search(name):
        return m.group(1) + b"_" + m.group(2).upper()
    if not real:
        return None
    if name.startswith(b"?"):
        word = name[1:].split(b"@", 1)[0]
    else:
        word = (name[1:] if name[:1] in (b"_", b"@") else name).split(b"@", 1)[0]
    return real.get(word)


def rename(data: bytes, names: dict[bytes, bytes], real: dict[bytes, bytes] | None = None) -> bytes:
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
        key = address_of(name, real or {})
        if key is not None and name != names.get(key):
            new = names.get(key)
            if new is None and key.startswith(b"D_"):
                new = b"_" + key  # data the original's code doesn't name
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
    info = json.loads(config.read_text(encoding="utf-8"))
    data = obj.read_bytes()
    obj.write_bytes(rename(data, wanted(info), real_names(info)))


if __name__ == "__main__":
    main()
