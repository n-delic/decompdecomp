"""PlayStation 2 programs: the game's main executable is an ELF for the Emotion Engine (e.g. SLUS_200.00 on the disc,
named by SYSTEM.CNF's BOOT2 line). Usually stripped, sometimes still with symbols."""

from __future__ import annotations

import collections
import hashlib
import io
import re
import struct

from ...arch.mips import JR_RA, OPCODE_SHIFT, RA, REGISTER_BITS, RS_SHIFT, RT_SHIFT, SD, SP, SPECIAL, SQ
from ...rom import RomError
from .disc import region_of

# ELF32's header and tables (little-endian on the PS2)
EM_MIPS = 8
ELF_HEADER_BYTES = 0x34
MACHINE_AT, PHOFF_AT, SHOFF_AT, PHENTSIZE_AT, SHENTSIZE_AT = 18, 28, 32, 42, 46  # where the header keeps them
SECTION_HEADER_BYTES = 40
SHT_NOBITS = 8  # a section with no bytes in the file (.bss)
PT_LOAD, PF_X = 1, 1  # a program header loaded into memory; its flag for executable
SHIFT_AND_FUNCT = 0x7FF  # a SPECIAL instruction's shift amount (zero for a move) and its function
DADDU_MOVE = 0x2D  # daddu with no shift: `daddu rd, rs, $zero` is how EE-GCC 2.95+ moves a register
# how code_style's numbers tell compilers apart (see guess_compiler)
STYLE_MIN_RETURNS = 20  # fewer returns than this is too little code to judge by
CODEWARRIOR_NOPS = 0.6  # share of returns with a nop in the delay slot: CodeWarrior 94%, GCC 9-19%
CODEWARRIOR_3_MOVES = 1  # daddu moves per return: CodeWarrior 3 moves with daddu, 2.x without
SN_SQ_SAVES = 0.5  # share of $ra saves made with sq: SN's EE-GCC 2.95.2
# What a compiler leaves in a program when its notes survive linking: (pattern in .comment, compiler guess)
_GCC = [(rb"GCC: \(GNU\) 2\.9-", "EEGCC-2.9"), (rb"GCC: \(GNU\) 2\.95\.2", "EEGCC-2.95.2"),
        (rb"GCC: \(GNU\) 2\.95\.3", "EEGCC-2.95.3"), (rb"GCC: \(GNU\) 3\.", "EEGCC-3.2")]


def is_mips_elf(data: bytes) -> bool:
    return (len(data) > ELF_HEADER_BYTES and data[:4] == b"\x7fELF" and data[5] == 1  # 32-bit
            and struct.unpack_from("<H", data, MACHINE_AT)[0] == EM_MIPS)


def _sections(data: bytes) -> dict[str, bytes]:
    """Section name -> contents, from the section headers (a stripped program usually still has them)."""
    shoff, = struct.unpack_from("<I", data, SHOFF_AT)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, SHENTSIZE_AT)
    if (not shoff or shentsize != SECTION_HEADER_BYTES or not shnum
            or shoff + SECTION_HEADER_BYTES * shnum > len(data) or shstrndx >= shnum):
        return {}
    heads = [struct.unpack_from("<IIIIII", data, shoff + SECTION_HEADER_BYTES * i) for i in range(shnum)]
    names = data[heads[shstrndx][4]:heads[shstrndx][4] + heads[shstrndx][5]]
    out = {}
    for name, kind, _, _, offset, size in heads:
        label = names[name:names.find(b"\0", name)].decode("latin-1")
        out[label] = b"" if kind == SHT_NOBITS else data[offset:offset + size]
    return out


def code_style(data: bytes, within: list[tuple[int, int]] | None = None) -> tuple[int, float, float, float]:
    """(returns, share of returns with a nop in the delay slot, `daddu rd, rs, $zero` moves per return, share of
    $ra saves made with sq) over the program's executable code, or only its code between the addresses `within`."""
    phoff, = struct.unpack_from("<I", data, PHOFF_AT)
    phentsize, phnum = struct.unpack_from("<HH", data, PHENTSIZE_AT)
    returns = nops = moves = sd = sq = 0
    for i in range(phnum):
        kind, off, vaddr, _, size, _, flags, _ = struct.unpack_from("<8I", data, phoff + phentsize * i)
        if kind != PT_LOAD or not flags & PF_X:
            continue
        words = struct.unpack_from(f"<{min(size, len(data) - off) // 4}I", data, off)
        for k, w in enumerate(words):
            if within is not None and not any(a <= vaddr + 4 * k < b for a, b in within):
                continue
            op, rs, rt = w >> OPCODE_SHIFT, (w >> RS_SHIFT) & REGISTER_BITS, (w >> RT_SHIFT) & REGISTER_BITS
            if w == JR_RA and k + 1 < len(words):
                returns += 1
                nops += words[k + 1] == 0
            elif op == SPECIAL and w & SHIFT_AND_FUNCT == DADDU_MOVE and rt == 0 and rs:
                moves += 1
            elif rs == SP and rt == RA:  # $ra saved to the stack
                sd += op == SD
                sq += op == SQ
    return returns, nops / (returns or 1), moves / (returns or 1), sq / ((sd + sq) or 1)


def _object_starts(data: bytes) -> tuple[dict[int, bool], dict[int, int]]:
    """Where each object file's code starts, whether GCC 2.x compiled it, and where the section it's in ends, from the
    symbol table: CodeWarrior's linker keeps a local .text section symbol at the start of every object's code, and GCC
    2.x leaves gcc2_compiled. there. ({}, {}) when the program has no symbol table."""
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile

    starts: dict[int, bool] = {}
    ends: dict[int, int] = {}
    try:
        elf = ELFFile(io.BytesIO(data))
        table = elf.get_section_by_name(".symtab")
        for s in table.iter_symbols() if table is not None else ():
            at, index = s["st_value"], s["st_shndx"]
            if isinstance(index, int) and (s.name == "gcc2_compiled." or (s.name == ".text"
                                                                           and s["st_info"]["type"] == "STT_SECTION")):
                starts[at] = starts.get(at, False) or s.name == "gcc2_compiled."
                section = elf.get_section(index)
                ends[at] = section["sh_addr"] + section["sh_size"]
    except (ELFError, ValueError, struct.error):
        return {}, {}
    return starts, ends


def object_compilers(data: bytes) -> list[tuple[int, int, str]]:
    """(start, end, compiler guess) of each stretch of object files one compiler built, for a program that kept its
    symbols and mixes GCC with CodeWarrior; [] otherwise. A 2002 game's 183 object files: 170 GCC's, with 2,128 of its
    2,284 functions (a nop after 14% of their returns, GCC's habit), and 13 without the mark, CodeWarrior's C library
    (memcpy, strlen: a nop after 87%) and hand-written kernel stubs. Objects without the mark count as CodeWarrior's
    only in a program that shows it (.mwcats from its linker, or its note): elsewhere they're hand-written."""
    sections = _sections(data)
    comment = sections.get(".comment", b"")
    if ".mwcats" not in sections and b"MW MIPS C Compiler" not in comment:
        return []
    starts, ends = _object_starts(data)
    if all(starts.values()) or not any(starts.values()):
        return []
    order = sorted(starts)
    spans = [(start, min(order[i + 1], ends[start]) if i + 1 < len(order) else ends[start]) for i, start in enumerate(order)]
    # which GCC: a version note, else SN's 2.95.2 by its $ra saved with sq in the GCC objects' own code (93% of a
    # 2001 game's, 0% of a launch game's Sony GCC; CodeWarrior's objects save it with sq too, so they don't count)
    sq = code_style(data, [s for s in spans if starts[s[0]]])[3]
    gcc = next((guess for pattern, guess in _GCC if re.search(pattern, comment)),
               "EEGCC-2.95.2" if sq > SN_SQ_SAVES else "EEGCC")
    mw = "MWCPS2-2" if re.search(rb"MW MIPS C Compiler \(2\.", comment) else "MWCPS2-3"
    out: list[tuple[int, int, str]] = []
    for start, end in spans:
        guess = gcc if starts[start] else mw
        if out and out[-1][2] == guess and out[-1][1] == start:
            out[-1] = (out[-1][0], end, guess)
        else:
            out.append((start, end, guess))
    return out


def guess_compiler(data: bytes) -> str:
    """Which compiler built the game. Its code says it best (what agents saw on GTA San Andreas, whose one
    "MW MIPS C Compiler" note comes from a library while its own code is EE-GCC 2.96's): CodeWarrior leaves the delay
    slot of a return empty (a nop after 94% of them in its demo builds; GCC 9-19%, GTA SA 11%), 2.x moves registers
    without daddu, and SN's EE-GCC 2.95.2 saves $ra with sq. A GCC version note picks among the GCCs; without one,
    EE-GCC 2.96, the PS2 SDK's (the import then tests the compilers on the code). Too little code: the notes alone.
    The style is only a first guess: a 2001 game's GCC objects (its symbols say so) leave 85% of those delay slots
    empty, as CodeWarrior does, and the import's test of every installed compiler decides. A program that kept its symbols and mixes compilers says which built each object file (object_compilers): the
    one that built most of the code is the game's."""
    if parts := object_compilers(data):
        built: collections.Counter = collections.Counter()
        for start, end, guess in parts:
            built[guess] += end - start
        return built.most_common(1)[0][0]
    sections = _sections(data)
    comment = sections.get(".comment", b"")
    gcc = next((guess for pattern, guess in _GCC if re.search(pattern, comment)), None)
    returns, nops, moves, sq = code_style(data)
    if returns >= STYLE_MIN_RETURNS:
        if nops > CODEWARRIOR_NOPS:
            return "MWCPS2-3" if moves > CODEWARRIOR_3_MOVES else "MWCPS2-2"
        return gcc or ("EEGCC-2.95.2" if sq > SN_SQ_SAVES else "EEGCC")
    if ".mwcats" in sections or b"MW MIPS C Compiler" in comment:
        return "MWCPS2-2" if re.search(rb"MW MIPS C Compiler \(2\.", comment) else "MWCPS2-3"
    return gcc or "EEGCC"


def read_elf(data: bytes, boot_name: str = "") -> tuple[bytes, dict]:
    if not is_mips_elf(data) or data[4] != 1:
        raise RomError("this isn't a PlayStation 2 program (expected a 32-bit little-endian MIPS ELF)")
    e_type, _, entry = struct.unpack_from("<HHI", data, 16)[0], 0, struct.unpack_from("<I", data, 24)[0]
    if e_type != 2:
        raise RomError("this ELF isn't an executable (it may be a library or an object file)")
    code = ""
    m = re.match(r"^([A-Z]{4})[_-](\d{3})\.?(\d{2})", boot_name.upper())
    if m:
        code = f"{m.group(1)}-{m.group(2)}{m.group(3)}"
    has_symbols = b".symtab" in data[-65536:] or b".symtab" in data[:65536]
    return data, {
        "title": code or boot_name or "PlayStation 2 game", "game_code": code, "region": region_of(code) if code else "Unknown region",
        "version": 0, "entrypoint": entry, "gp": 0, "crc1": 0, "crc2": 0, "size": len(data),
        "sha1": hashlib.sha1(data).hexdigest(), "byte_order": "elf", "compiler_guess": guess_compiler(data), "platform": "ps2",
        "boot": boot_name, "symbols": has_symbols,
        "compiler_parts": [list(part) for part in object_compilers(data)],  # after_import gives them their ranges
    }
