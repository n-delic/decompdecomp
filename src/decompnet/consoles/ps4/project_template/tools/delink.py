#!/usr/bin/env python3
"""Cut the game's functions out of your copy of the eboot into one ELF64 object file per source file, for objdiff.

    python tools/delink.py config/<version>/functions.json OUT_DIR

A unit's object holds the game's bytes for that range of code unchanged (so a reference from one function to another
of the unit keeps its distance), each function as a global FUNC symbol at its own offset, and a relocation for every
reference that leaves its function: calls and jumps, rip-relative data addresses, absolute immediates. objdiff masks
the bytes a relocation fills, so the original's absolute address and the C's symbol compare as the same thing.
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
    """One unit's object: .text (the game's bytes), .rela.text, .symtab, .strtab, .shstrtab."""
    text = bytearray(blob)
    # the symbols: the unit's functions first (global), then whatever their code refers to (undefined)
    symbols: list[tuple[str, int, int, int, int, int]] = []  # (name, info, shndx, value, size, section of name)
    externs: dict[str, int] = {}
    for fn in functions:
        at = fn["addr"] - start
        externs[fn["name"]] = len(symbols) + 1  # index zero is the ELF null symbol
        symbols.append((fn["name"], (STB_GLOBAL << 4) | STT_FUNC, 1, at, fn["size"], 0))
    relocs = bytearray()
    for index, fn in enumerate(functions):
        for off, rtype, name, addend in fn.get("relocs") or []:
            if not name:  # a label inside the function: nothing to relocate across objects
                continue
            at = fn["addr"] - start + off
            size = RELOC_WIDTH.get(rtype)
            if size is None or off < 0 or off + size > fn["size"] or at < 0 or at + size > len(text):
                raise ValueError(f"invalid relocation field in {fn['name']} at {off}: type {rtype}")
            text[at:at + size] = bytes(size)  # the addend lives in the relocation; objdiff masks the field
            if name not in externs:
                externs[name] = len(symbols) + 1
                symbols.append((name, (STB_GLOBAL << 4) | STT_NOTYPE, 0, 0, 0, 0))
            relocs += struct.pack("<QQq", at, (externs[name] << 32) | rtype, addend)

    # strings and the symbol table
    strtab = bytearray(b"\0")
    table = bytearray(24)  # index 0: the null symbol
    for name, info, shndx, value, size, _ in symbols:
        at = struct.pack("<I", len(strtab))
        strtab += name.encode("utf-8") + b"\0"
        table += at + struct.pack("<BBHQQ", info, 0, shndx, value, size)
    first_global = 1  # every symbol here is global

    shstr = bytearray(b"\0")
    names = {}
    for sec in (".text", ".rela.text", ".symtab", ".strtab", ".shstrtab"):
        names[sec] = len(shstr)
        shstr += sec.encode() + b"\0"

    out = bytearray(64)  # the ELF header, filled once the offsets are known
    _align(out, 16)
    text_at = len(out)
    out += text
    _align(out, 8)
    rela_at = len(out)
    out += relocs
    _align(out, 8)
    sym_at = len(out)
    out += table
    str_at = len(out)
    out += strtab
    shstr_at = len(out)
    out += shstr
    _align(out, 8)
    sh_at = len(out)
    out += _section(0, EMPTY, 0, 0, 0, 0)
    out += _section(names[".text"], 1, SHF_ALLOC | SHF_EXECINSTR, 0, text_at, len(text), 0, 0, 16)
    out += _section(names[".rela.text"], SHT_RELA, SHF_INFO_LINK, 0, rela_at, len(relocs), 3, 1, 8, 24)
    out += _section(names[".symtab"], 2, 0, 0, sym_at, len(table), 4, first_global, 8, 24)
    out += _section(names[".strtab"], 3, 0, 0, str_at, len(strtab))
    out += _section(names[".shstrtab"], 3, 0, 0, shstr_at, len(shstr))

    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    struct.pack_into("<16sHHIQQQIHHHHHH", out, 0, ident, ET_REL, EM_X86_64, 1, 0, 0, sh_at, 0, 64, 0, 0, 64, 6, 5)
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
