"""#312: a switch's jump table can be tail data of an overlay, with no symbol of its own.

spimdisasm only writes `%hi`/`%lo` when it has a name for the address. When the table is swallowed into the
end of another function (or an overlay's tail), it emits the `lui`/`lw` pair as plain numbers and
`target_from_split` records no relocation. The judge must still put the candidate's own .rodata at the ROM's
real table address and compare its bytes there -- a raw constant pair, which the candidate has no relocation
for, must stay untouched (it is not a data reference).
"""

import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.rom import match
from decompnet.rom.split import AsmLine, RomFunction
from decompnet.rom.symbols import Symbol, SymbolTable

pytestmark = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None,
                                reason="needs gcc-2.7.2-kmc to assemble the candidate")

VRAM, TABLE_OFFSET, ROM_BASE = 0x80129000, 0x10, 0x1000
TABLE = VRAM + TABLE_OFFSET  # the table, right after the function: tail data of its overlay
TABLE_WORD = 0x12345678

# What IDO emits: the switch table in the object's own .rodata, reached with a hi/lo pair through $at.
CANDIDATE = """
    .set noreorder
    .set noat
    .text
    .globl pick
pick:
    lui $at, %hi(tbl)
    lw  $v0, %lo(tbl)($at)
    jr  $ra
    nop
    .section .rodata
tbl:
    .word 0x12345678
"""


def _candidate(tmp_path):
    tc = toolchains.require("gcc-2.7.2-kmc")
    asm = tmp_path / "pick.s"
    asm.write_text(CANDIDATE)
    obj = tmp_path / "pick.o"
    subprocess.run([str(tc / "as"), "-mips3", "-G0", "-EB", "-o", str(obj), str(asm)], check=True,
                   capture_output=True)
    return obj.read_bytes()


def _target():
    """The ROM's function, disassembled with raw immediates: spimdisasm had no symbol for the table."""
    hi, lo = (TABLE + 0x8000) >> 16, TABLE & 0xFFFF
    words = [(0x0F << 26) | (1 << 16) | hi,  # lui $at, hi
             (0x23 << 26) | (1 << 21) | (2 << 16) | lo,  # lw $v0, lo($at)
             0x03E00008,  # jr $ra
             0]  # nop
    lines = [AsmLine(ROM_BASE + 4 * i, VRAM + 4 * i, word,
                     ("lui", "lw", "jr", "nop")[i], "") for i, word in enumerate(words)]
    return RomFunction("pick", VRAM, ROM_BASE, "ovl", lines, Path("pick.s"))


def _judge(obj, table_word=TABLE_WORD):
    syms = SymbolTable([Symbol("pick", VRAM, 0x10, "func")])
    target = match.target_from_split(_target(), syms.resolve)
    code = b"".join(ln.word.to_bytes(4, "big") for ln in _target().lines)
    rom = bytearray(ROM_BASE + 0x100)
    rom[ROM_BASE:ROM_BASE + len(code)] = code
    rom[ROM_BASE + TABLE_OFFSET:ROM_BASE + TABLE_OFFSET + 4] = table_word.to_bytes(4, "big")

    def read(addr, n):
        if VRAM <= addr and addr + n <= VRAM + len(rom) - ROM_BASE:
            return bytes(rom[ROM_BASE + addr - VRAM:ROM_BASE + addr - VRAM + n])
        return None

    return match.link(obj, "pick", target, syms, read), target


def test_a_table_without_a_symbol_is_placed_at_the_roms_address(tmp_path):
    result, target = _judge(_candidate(tmp_path))
    assert target.relocs == []  # nothing annotated: the bug's starting point
    assert result.exact, result.problems
    assert result.placements[".rodata"][0] == TABLE  # its own table lands on the ROM's table


def test_the_tables_bytes_are_still_compared_with_the_rom(tmp_path):
    result, _ = _judge(_candidate(tmp_path), table_word=TABLE_WORD ^ 0xFF)
    assert not result.exact
    assert any("differs from the ROM" in p for p in result.problems), result.problems


def test_no_relocation_is_invented_for_a_raw_constant(tmp_path):
    """A `lui`/`lw` pair that loads a plain 32-bit constant has no relocation in the object. The target has no
    annotation either; both sides must stay reloc-less so the diff rows of a function that already matches do
    not change (an earlier fix that inferred a reloc from every raw pair broke six such functions' diffs)."""
    hi, lo = (TABLE + 0x8000) >> 16, TABLE & 0xFFFF
    asm = tmp_path / "constant.s"
    asm.write_text(f"""
        .set noreorder
        .set noat
        .text
        .globl pick
pick:
        lui $at, {hi:#x}
        lw  $v0, {lo - 0x10000:#x}($at)
        jr  $ra
        nop
    """)
    tc = toolchains.require("gcc-2.7.2-kmc")
    obj = tmp_path / "constant.o"
    subprocess.run([str(tc / "as"), "-mips3", "-G0", "-EB", "-o", str(obj), str(asm)], check=True,
                   capture_output=True)
    result, target = _judge(obj.read_bytes())
    assert target.relocs == []
    assert result.exact, result.problems
    from decompnet import scoring
    d = scoring.diff(match.canonical_target(target, SymbolTable([Symbol("pick", VRAM, 0x10, "func")])),
                     result.candidate)
    assert d.score == 0, [(r[1], r[2]) for r in d.rows if r[0]]
