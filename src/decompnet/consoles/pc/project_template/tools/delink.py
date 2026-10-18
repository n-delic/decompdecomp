#!/usr/bin/env python3
"""Cut the game's functions out of your copy of the program into one object file per source file (COFF, as MSVC
writes them), for objdiff to compare with what src/ compiles to.

    python tools/delink.py config/<version>/functions.json OUT_DIR

functions.json (written by decompdecomp from Ghidra's analysis) says where each function is in the .exe and where its
code refers to addresses: calls, jumps and data addresses become relocations, as in a compiled object, so a function
matches whatever names your C uses for them. Each function gets a section of its own, as MSVC's /Gy makes them.
Standard library only. Made by decompdecomp.
"""

import hashlib
import json
import struct
import sys
from pathlib import Path

DIR32, REL32 = 0x06, 0x14
CODE = 0x60500020  # code, readable, executable, 16-byte aligned


def coff(functions: list[dict], exe: bytes) -> bytes:
    symbols: list[tuple[str, int, int, int, int]] = []  # (name, value, section, type, storage class)
    externs: dict[str, int] = {}
    sections = []
    for i, fn in enumerate(functions):
        symbols.append((fn["name"], 0, i + 1, 0x20, 2))
    for i, fn in enumerate(functions):
        code = bytearray(exe[fn["offset"]:fn["offset"] + fn["size"]])
        relocs = bytearray()
        labels: dict[int, int] = {}
        for off, rtype, name, addend in fn["relocs"]:
            if name is None:  # an address inside this function (a jump table, its entries): a label, like MSVC's $L
                if addend not in labels:
                    labels[addend] = len(symbols)
                    symbols.append((f"$L{fn['addr'] + addend:08X}", addend, i + 1, 0, 6))
                index, inline = labels[addend], 0
            else:
                if name not in externs:
                    externs[name] = len(symbols)
                    symbols.append((name, 0, 0, 0x20 if rtype == REL32 else 0, 2))
                index, inline = externs[name], addend
            struct.pack_into("<i", code, off, inline)
            relocs += struct.pack("<IIH", off, index, rtype)
        sections.append((bytes(code), bytes(relocs), len(fn["relocs"])))

    strings = bytearray(4)
    table = bytearray()
    for name, value, section, typ, cls in symbols:
        raw = name.encode()
        if len(raw) <= 8:
            field = raw.ljust(8, b"\0")
        else:
            field = struct.pack("<II", 0, len(strings))
            strings += raw + b"\0"
        table += struct.pack("<8sIhHBB", field, value, section, typ, cls, 0)
    struct.pack_into("<I", strings, 0, len(strings))

    at = 20 + 40 * len(sections)
    headers, body = bytearray(), bytearray()
    for code, relocs, count in sections:
        headers += struct.pack("<8sIIIIIIHHI", b".text", 0, 0, len(code), at, at + len(code) if count else 0, 0,
                               count, 0, CODE)
        body += code + relocs
        at += len(code) + len(relocs)
    return struct.pack("<HHIIIHH", 0x14C, len(sections), 0, at, len(symbols), 0, 0) + headers + body + table + strings


def main() -> None:
    config, out = Path(sys.argv[1]), Path(sys.argv[2])
    info = json.loads(config.read_text(encoding="utf-8"))
    exe = Path(info["exe"]).read_bytes()
    if hashlib.sha1(exe).hexdigest() != info["sha1"]:
        sys.exit(f"{info['exe']} isn't the copy this project was made from (its SHA-1 should be {info['sha1']})")
    out.mkdir(parents=True, exist_ok=True)
    for unit, functions in info["units"].items():
        data = coff(functions, exe)
        path = out / f"{unit}.obj"
        if not path.exists() or path.read_bytes() != data:  # unchanged objects keep their time: nothing rebuilds
            path.write_bytes(data)


if __name__ == "__main__":
    main()
