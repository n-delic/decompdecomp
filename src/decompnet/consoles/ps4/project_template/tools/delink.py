#!/usr/bin/env python3
"""Cut the game's functions out of your copy of the eboot into one ELF64 object file per source file, for objdiff.

    python tools/delink.py config/<version>/functions.json OUT_DIR

A unit's object holds each function's bytes in its own section, like the compiled candidate, with a relocation for
every reference that leaves it: calls and jumps, rip-relative data addresses, absolute immediates. Relocation fields
are zeroed and their addends live in RELA entries; the destination symbol and addend remain part of strict matching.
Standard library only. Made by decompdecomp.
"""

import hashlib
import json
import struct
import sys
from pathlib import Path

R_X86_64_64, R_X86_64_PC32, R_X86_64_PLT32, R_X86_64_32 = 1, 2, 4, 10
RELOC_WIDTH = {1: 8, 2: 4, 4: 4, 10: 4, 11: 4, 15: 1}
ET_REL, EM_X86_64, SHT_RELA = 1, 62, 4
SHF_ALLOC, SHF_EXECINSTR, SHF_INFO_LINK = 0x2, 0x4, 0x40
STB_LOCAL, STB_GLOBAL, STT_NOTYPE, STT_FUNC = 0, 1, 0, 2
EMPTY = 0


def _align(data: bytearray, n: int) -> None:
    while len(data) % n:
        data.append(0)


def _section(name: int, stype: int, flags: int, addr: int, offset: int, size: int, link: int = 0, info: int = 0,
             addralign: int = 1, entsize: int = 0) -> bytes:
    return struct.pack("<IIQQQQIIQQ", name, stype, flags, addr, offset, size, link, info, addralign, entsize)


def elf64(unit: str, functions: list[dict], blob: bytes, start: int) -> bytes:
    """One independently relocatable text section per function, matching Clang's -ffunction-sections (#119).

    Inter-function gaps are alignment, not instructions of either function. Original addresses are kept in a
    non-allocated metadata section for explicit guard binding; symbol values remain section-relative ELF values.
    """
    symbols = []
    externs = {}
    addresses = {}
    for index, fn in enumerate(functions):
        if fn["name"] in externs:
            raise ValueError(f"duplicate function symbol in {unit}: {fn['name']}")
        externs[fn["name"]] = len(symbols) + 1  # index zero is the ELF null symbol
        symbols.append((fn["name"], (STB_GLOBAL << 4) | STT_FUNC, 1 + index * 2, 0, fn["size"]))
        addresses[fn["name"]] = fn["addr"]

    sym_index = 2 * len(functions) + 2  # null, text/rela pairs, original-address metadata, then symbol table
    sections = []
    for index, fn in enumerate(functions):
        at, size = fn["addr"] - start, fn["size"]
        if at < 0 or size <= 0 or at + size > len(blob):
            raise ValueError(f"invalid function extent in {unit}: {fn['name']}")
        text = bytearray(blob[at:at + size])
        relocs = bytearray()
        for off, rtype, name, addend in fn.get("relocs") or []:
            if not name:
                continue
            width = RELOC_WIDTH.get(rtype)
            if width is None or off < 0 or off + width > size:
                raise ValueError(f"invalid relocation field in {fn['name']} at {off}: type {rtype}")
            text[off:off + width] = bytes(width)
            if name not in externs:
                externs[name] = len(symbols) + 1
                symbols.append((name, (STB_GLOBAL << 4) | STT_NOTYPE, 0, 0, 0))
            relocs += struct.pack("<QQq", off, (externs[name] << 32) | rtype, addend)
        section = ".text" if index == 0 else f".text.{fn['name']}"
        sections.append((section, 1, SHF_ALLOC | SHF_EXECINSTR, bytes(text), 0, 0, 16, 0))
        sections.append((f".rela{section}", SHT_RELA, SHF_INFO_LINK, bytes(relocs), sym_index, 1 + index * 2, 8, 24))

    strtab = bytearray(b"\0")
    table = bytearray(24)
    for name, info, shndx, value, size in symbols:
        table += struct.pack("<IBBHQQ", len(strtab), info, 0, shndx, value, size)
        strtab += name.encode("utf-8") + b"\0"
    sections.extend([
        (".decomp.addresses", 1, 0, json.dumps(addresses, sort_keys=True).encode("utf-8"), 0, 0, 1, 0),
        (".symtab", 2, 0, bytes(table), sym_index + 1, 1, 8, 24),
        (".strtab", 3, 0, bytes(strtab), 0, 0, 1, 0),
    ])
    shstr = bytearray(b"\0")
    names = {}
    for name in [*(s[0] for s in sections), ".shstrtab"]:
        names[name] = len(shstr)
        shstr += name.encode("utf-8") + b"\0"
    sections.append((".shstrtab", 3, 0, bytes(shstr), 0, 0, 1, 0))

    out = bytearray(64)
    headers = [_section(0, EMPTY, 0, 0, 0, 0)]
    for name, kind, flags, data, link, info, alignment, entsize in sections:
        _align(out, alignment)
        headers.append(_section(names[name], kind, flags, 0, len(out), len(data), link, info, alignment, entsize))
        out += data
    _align(out, 8)
    sh_at = len(out)
    out += b"".join(headers)
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    struct.pack_into("<16sHHIQQQIHHHHHH", out, 0, ident, ET_REL, EM_X86_64, 1, 0, 0, sh_at, 0, 64,
                     0, 0, 64, len(headers), len(headers) - 1)
    return bytes(out)

def main() -> None:
    config, out = Path(sys.argv[1]), Path(sys.argv[2])
    info = json.loads(config.read_text(encoding="utf-8"))
    data = Path(info["elf"]).read_bytes()
    if hashlib.sha1(data).hexdigest() != info["sha1"]:
        sys.exit(f"{info['elf']} isn't the copy this project was made from (its SHA-1 should be {info['sha1']})")
    out.mkdir(parents=True, exist_ok=True)
    for unit, functions in info["units"].items():
        start = functions[0]["addr"]
        end = max(fn["addr"] + fn["size"] for fn in functions)
        blob = data[functions[0]["offset"]:functions[0]["offset"] + (end - start)]
        obj = elf64(unit, functions, blob, start)
        path = out / f"{unit}.o"
        if not path.exists() or path.read_bytes() != obj:  # unchanged objects keep their time: nothing rebuilds
            path.write_bytes(obj)


if __name__ == "__main__":
    main()
