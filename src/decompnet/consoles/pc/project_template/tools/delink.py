#!/usr/bin/env python3
"""Cut the game's functions out of your copy of the program into one object file per source file (COFF, as MSVC
writes them), for objdiff to compare with what src/ compiles to.

    python tools/delink.py config/<version>/functions.json OUT_DIR

functions.json (written by decompdecomp from Ghidra's analysis) says where each function is in the .exe and where its
code refers to addresses: calls, jumps and data addresses become relocations, as in a compiled object, so a function
matches whatever names your C uses for them. Each function gets a section of its own, as MSVC's /Gy makes them. A
64-bit program (an x64 one, PE32+) comes out an AMD64 object: the same records (measured on Visual C++ 19.44's x64
objects), but the compiler keeps 0 in every field a relocation names — nothing inline — and only an address taken
whole (ADDR64) is 8 bytes.
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
# directory; each relocation block's entries are 16 bits, the type in the top 4 (HIGHLOW: a 32-bit address). PE32+
# (a 64-bit program's optional header) keeps its image base as 8 bytes 24 bytes in, its data directories 16 bytes
# later: its base relocations are DIR64, an 8-byte address
PE_OFFSET_AT, OPTIONAL_HEADER_AT, IMAGE_BASE_AT, BASE_RELOCATIONS_AT = 0x3C, 0x18, 28, 136
PE32PLUS, PE32PLUS_IMAGE_BASE_AT, PE32PLUS_BASE_RELOCATIONS_AT = 0x20B, 24, 152
SECTION_COUNT_AT, OPTIONAL_HEADER_SIZE_AT, SECTION_HEADER_BYTES = 6, 20, 40
HIGHLOW, RELOC_BLOCK_HEADER_BYTES = 3, 8
DIR64 = 10  # a 64-bit program's base relocation: HIGHLOW's, naming an 8-byte address
I386, AMD64 = 0x14C, 0x8664  # the machine number of the object written: a 32-bit x86 program's, a 64-bit x64 one's
# a 64-bit program's relocations, measured on Visual C++ 19.44's x64 object (whose symbol, section and relocation
# records are laid out as the 32-bit ones): every relocatable field holds zero, its target the symbol. ADDR64 is an
# 8-byte address, ADDR32NB a 4-byte image-relative one (a switch table's address and its entries), and REL32 ..
# REL32_LAST (0x04..0x09) the 4-byte PC-relative family, by how many instruction bytes follow the field
X64_ADDR64, X64_ADDR32NB, X64_REL32, X64_REL32_LAST = 0x01, 0x03, 0x04, 0x09


def pe32_plus(data: bytes) -> bool:
    """Whether a program's optional header is PE32+ (a 64-bit, x64 one): its magic number says. Each program's
    functions are cut from its own bytes, so that's what tells the two apart; bytes that aren't a program (a unit's
    code alone) are the 32-bit kind, as they always were here."""
    pe = struct.unpack_from("<I", data, PE_OFFSET_AT)[0] if len(data) >= PE_OFFSET_AT + 4 else 0
    return len(data) >= pe + OPTIONAL_HEADER_AT + 2 and \
        struct.unpack_from("<H", data, pe + OPTIONAL_HEADER_AT)[0] == PE32PLUS


def coff(functions: list[dict], exe: bytes) -> bytes:
    plus = pe32_plus(exe)  # an x64 program's object: its machine number and its relocations, not its records
    calls = range(X64_REL32, X64_REL32_LAST + 1) if plus else (REL32,)  # what reaches a function: a call or jump
    symbols: list[tuple[str, int, int, int, int]] = []  # (name, value, section, type, storage class)
    externs: dict[str, int] = {}
    sections = []
    function_names = {fn["name"] for fn in functions}
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
                    # the x64 compiler's labels ($LN12) count from the function's start, as the analyzer's addends
                    # do; a 32-bit object names one by its address
                    symbols.append((f"$L{addend:08X}" if plus else f"$L{fn['addr'] + addend:08X}",
                                    addend, i + 1, 0, 6))
                index, inline = labels[addend], 0
            else:
                if name not in externs:
                    externs[name] = len(symbols)
                    # an undefined symbol's type: a function's on the 32-bit path (only a call's REL32 reaches one);
                    # on x64 a REL32 reaches data too (rip-relative), so its name says which it is
                    function = name in function_names if plus else rtype in calls
                    symbols.append((name, 0, 0, 0x20 if function else 0, 2))
                index, inline = externs[name], 0 if plus else addend
            # a 64-bit compiler keeps 0 in every relocatable field, its target a symbol (32-bit keeps an addend
            # inline); ADDR64's field is 8 bytes, every other relocation's 4
            struct.pack_into("<q" if plus and rtype == X64_ADDR64 else "<i", code, off, inline)
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
    return struct.pack("<HHIIIHH", AMD64 if plus else I386, len(sections), 0, at, len(symbols), 0, 0) + \
        headers + body + table + strings


def rebase(data: bytes, base: int) -> bytes:
    """The program as Windows loads it at `base` instead of the address its header prefers: every address its base
    relocations name moved by the difference, and its header saying the new base."""
    out = bytearray(data)
    pe = struct.unpack_from("<I", out, PE_OFFSET_AT)[0]
    opt = pe + OPTIONAL_HEADER_AT
    # PE32+ keeps the layout with wider words: its image base and the addresses its base relocations name are 8
    # bytes (the base 24 bytes into its optional header, the directory at 152), its relocations DIR64
    plus = pe32_plus(out)
    word, mask = ("<Q", (1 << 64) - 1) if plus else ("<I", 0xFFFFFFFF)
    image_base_at = PE32PLUS_IMAGE_BASE_AT if plus else IMAGE_BASE_AT
    delta = (base - struct.unpack_from(word, out, opt + image_base_at)[0]) & mask
    if not delta:
        return data
    struct.pack_into(word, out, opt + image_base_at, base)
    count, opt_size = (struct.unpack_from("<H", out, pe + at)[0] for at in (SECTION_COUNT_AT, OPTIONAL_HEADER_SIZE_AT))
    sections = [struct.unpack_from("<III", out, opt + opt_size + SECTION_HEADER_BYTES * i + 12) for i in range(count)]

    def offset(rva: int):  # its place in the file, or None (sections: va, bytes in the file, where in the file)
        return next((at + rva - va for va, size, at in sections if va <= rva < va + size), None)
    rva, size = struct.unpack_from("<II", out, opt + (PE32PLUS_BASE_RELOCATIONS_AT if plus else BASE_RELOCATIONS_AT))
    kind = DIR64 if plus else HIGHLOW  # what one of its base relocations names: 8 bytes, or 4
    done = 0
    while done + RELOC_BLOCK_HEADER_BYTES <= size and (block := offset(rva + done)) is not None:
        page, length = struct.unpack_from("<II", out, block)
        if length < RELOC_BLOCK_HEADER_BYTES:
            break
        for k in range(RELOC_BLOCK_HEADER_BYTES, length - 1, 2):
            entry = struct.unpack_from("<H", out, block + k)[0]
            if entry >> 12 == kind and (at := offset(page + (entry & 0xFFF))) is not None:
                struct.pack_into(word, out, at, (struct.unpack_from(word, out, at)[0] + delta) & mask)
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
