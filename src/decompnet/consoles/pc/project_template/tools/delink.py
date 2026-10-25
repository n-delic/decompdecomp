#!/usr/bin/env python3
"""Cut the game's functions out of your copy of the program into one object file per source file (COFF, as MSVC
writes them), for objdiff to compare with what src/ compiles to.

    python tools/delink.py config/<version>/functions.json OUT_DIR

functions.json (written by decompdecomp from Ghidra's analysis) says where each function is in the .exe and where its
code refers to addresses: calls, jumps and data addresses become relocations, as in a compiled object, so a function
matches whatever names your C uses for them. Each function gets a section of its own, as MSVC's /Gy makes them.
The game's own DLLs are programs of the project too (functions.json's "modules"): a DLL's source files are in a
folder of its name (src/auto/<module>/), and its functions are cut out of it at the address the project gives it
(its preferred one, unless the program or another DLL has that: then moved by its base relocations, as Windows
moves it). Standard library only. Made by decompdecomp.
"""

import hashlib
import json
import struct
import sys
from pathlib import Path

DIR32, REL32 = 0x06, 0x14
CODE = 0x60500020  # code, readable, executable, 16-byte aligned
# in a Windows program: where the PE header is, then in its optional header the image base and the base relocations'
# directory; each relocation block's entries are 16 bits, the type in the top 4 (HIGHLOW: a 32-bit address)
PE_OFFSET_AT, OPTIONAL_HEADER_AT, IMAGE_BASE_AT, BASE_RELOCATIONS_AT = 0x3C, 0x18, 28, 136
SECTION_COUNT_AT, OPTIONAL_HEADER_SIZE_AT, SECTION_HEADER_BYTES = 6, 20, 40
HIGHLOW, RELOC_BLOCK_HEADER_BYTES = 3, 8


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


def rebase(data: bytes, base: int) -> bytes:
    """The program as Windows loads it at `base` instead of the address its header prefers: every address its base
    relocations name moved by the difference, and its header saying the new base."""
    out = bytearray(data)
    pe = struct.unpack_from("<I", out, PE_OFFSET_AT)[0]
    opt = pe + OPTIONAL_HEADER_AT
    delta = (base - struct.unpack_from("<I", out, opt + IMAGE_BASE_AT)[0]) & 0xFFFFFFFF
    if not delta:
        return data
    struct.pack_into("<I", out, opt + IMAGE_BASE_AT, base)
    count, opt_size = (struct.unpack_from("<H", out, pe + at)[0] for at in (SECTION_COUNT_AT, OPTIONAL_HEADER_SIZE_AT))
    sections = [struct.unpack_from("<III", out, opt + opt_size + SECTION_HEADER_BYTES * i + 12) for i in range(count)]

    def offset(rva: int):  # its place in the file, or None (sections: va, bytes in the file, where in the file)
        return next((at + rva - va for va, size, at in sections if va <= rva < va + size), None)
    rva, size = struct.unpack_from("<II", out, opt + BASE_RELOCATIONS_AT)
    done = 0
    while done + RELOC_BLOCK_HEADER_BYTES <= size and (block := offset(rva + done)) is not None:
        page, length = struct.unpack_from("<II", out, block)
        if length < RELOC_BLOCK_HEADER_BYTES:
            break
        for k in range(RELOC_BLOCK_HEADER_BYTES, length - 1, 2):
            entry = struct.unpack_from("<H", out, block + k)[0]
            if entry >> 12 == HIGHLOW and (at := offset(page + (entry & 0xFFF))) is not None:
                struct.pack_into("<I", out, at, (struct.unpack_from("<I", out, at)[0] + delta) & 0xFFFFFFFF)
        done += length
    return bytes(out)


def programs(info: dict) -> dict:
    """Each program of the project by the folder its source files are in ("" for the .exe's): (its file, its
    SHA-1, the address the project gives it; None: where its header says)."""
    out = {"": (info["exe"], info.get("sha1"), None)}
    for m in info.get("modules", []):
        out[m["name"]] = (m["exe"], m["sha1"], m["image_base"])
    return out


def module(unit: str) -> str:
    """The program a unit is of: a DLL's are in its folder (hw/text_10001000), the .exe's in none."""
    return unit.rsplit("/", 1)[0] if "/" in unit else ""


def main() -> None:
    config, out = Path(sys.argv[1]), Path(sys.argv[2])
    info = json.loads(config.read_text(encoding="utf-8"))
    loaded = {}
    for name, (path, sha1, base) in programs(info).items():
        data = Path(path).read_bytes()
        if hashlib.sha1(data).hexdigest() != sha1:
            sys.exit(f"{path} isn't the copy this project was made from (its SHA-1 should be {sha1})")
        loaded[name] = data if base is None else rebase(data, base)
    for unit, functions in info["units"].items():
        data = coff(functions, loaded[module(unit)])
        path = out / f"{unit}.obj"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != data:  # unchanged objects keep their time: nothing rebuilds
            path.write_bytes(data)


if __name__ == "__main__":
    main()
