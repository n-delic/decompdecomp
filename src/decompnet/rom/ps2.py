"""PlayStation 2 programs: the game's main executable is an ELF for the Emotion Engine (e.g. SLUS_200.00 on the disc,
named by SYSTEM.CNF's BOOT2 line). Usually stripped, sometimes still with symbols."""

from __future__ import annotations

import hashlib
import re
import struct

from .n64 import RomError
from .psx import region_of  # Sony's product codes, the same on both PlayStations

EM_MIPS = 8
# What a compiler leaves in a program when its notes survive linking: (pattern in .comment, compiler guess)
_GCC = [(rb"GCC: \(GNU\) 2\.9-", "EEGCC-2.9"), (rb"GCC: \(GNU\) 2\.95\.2", "EEGCC-2.95.2"),
        (rb"GCC: \(GNU\) 2\.95\.3", "EEGCC-2.95.3"), (rb"GCC: \(GNU\) 3\.", "EEGCC-3.2")]


def is_mips_elf(data: bytes) -> bool:
    return len(data) > 0x34 and data[:4] == b"\x7fELF" and data[5] == 1 and struct.unpack_from("<H", data, 18)[0] == EM_MIPS


def _sections(data: bytes) -> dict[str, bytes]:
    """Section name -> contents, from the section headers (a stripped program usually still has them)."""
    shoff, = struct.unpack_from("<I", data, 32)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 46)
    if not shoff or shentsize != 40 or not shnum or shoff + 40 * shnum > len(data) or shstrndx >= shnum:
        return {}
    heads = [struct.unpack_from("<IIIIII", data, shoff + 40 * i) for i in range(shnum)]
    names = data[heads[shstrndx][4]:heads[shstrndx][4] + heads[shstrndx][5]]
    out = {}
    for name, kind, _, _, offset, size in heads:
        label = names[name:names.find(b"\0", name)].decode("latin-1")
        out[label] = b"" if kind == 8 else data[offset:offset + size]  # 8: SHT_NOBITS
    return out


def code_style(data: bytes) -> tuple[int, float, float, float]:
    """(returns, share of returns with a nop in the delay slot, `daddu rd, rs, $zero` moves per return, share of
    $ra saves made with sq) over the program's executable code."""
    phoff, = struct.unpack_from("<I", data, 28)
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    returns = nops = moves = sd = sq = 0
    for i in range(phnum):
        kind, off, _, _, size, _, flags, _ = struct.unpack_from("<8I", data, phoff + phentsize * i)
        if kind != 1 or not flags & 1:  # loaded and executable
            continue
        words = struct.unpack_from(f"<{min(size, len(data) - off) // 4}I", data, off)
        for k, w in enumerate(words):
            op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
            if w == 0x03E00008 and k + 1 < len(words):  # jr $ra
                returns += 1
                nops += words[k + 1] == 0
            elif op == 0 and w & 0x7FF == 0x2D and rt == 0 and rs:
                moves += 1
            elif rs == 29 and rt == 31:
                sd += op == 0x3F
                sq += op == 0x1F
    return returns, nops / (returns or 1), moves / (returns or 1), sq / ((sd + sq) or 1)


def guess_compiler(data: bytes) -> str:
    """Which compiler built the game. Its code says it best (what agents saw on GTA San Andreas, whose one
    "MW MIPS C Compiler" note comes from a library while its own code is EE-GCC 2.96's): CodeWarrior leaves the delay
    slot of a return empty (a nop after 94% of them in its demo builds; GCC 9-19%, GTA SA 11%), 2.x moves registers
    without daddu, and SN's EE-GCC 2.95.2 saves $ra with sq. A GCC version note picks among the GCCs; without one,
    EE-GCC 2.96, the PS2 SDK's (the import then tests the GCCs on the code). Too little code: the notes alone."""
    sections = _sections(data)
    comment = sections.get(".comment", b"")
    gcc = next((guess for pattern, guess in _GCC if re.search(pattern, comment)), None)
    returns, nops, moves, sq = code_style(data)
    if returns >= 20:
        if nops > 0.6:
            return "MWCPS2-3" if moves > 1 else "MWCPS2-2"
        return gcc or ("EEGCC-2.95.2" if sq > 0.5 else "EEGCC")
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
    }
