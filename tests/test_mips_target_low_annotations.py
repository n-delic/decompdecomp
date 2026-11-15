"""Incomplete disassembly annotations must not create fictitious relocation differences."""
import struct
from pathlib import Path

import pytest

from decompnet.rom import match, split
from decompnet.rom.symbols import Symbol, SymbolTable

BASE = 0x80012000
ADDRESS = BASE + 0x12340
WORDS = (0x3c048002, 0x8c844340)


def function(words=WORDS, operand='%lo(table + (0x12340 & 0xFFFF))'):
    lines = [split.AsmLine(0, 0x80001000, words[0], 'lui', '$a0, %hi(table + 0x12340)'),
             split.AsmLine(4, 0x80001004, words[1], 'lw', '$a0, '+operand+'($a0)')]
    return split.RomFunction('example', 0x80001000, 0, 'code', lines, Path('example.s'))


@pytest.mark.parametrize('big', [True, False])
@pytest.mark.parametrize('mask', ['0xFFFF', '0xffff', '65535'])
def test_masked_addend_recovers_only_raw_proven_full_address(big, mask):
    fn = function(operand=f'%lo(table + (0x12340 & {mask}))')
    target = match.target_from_split(fn, {'table': BASE}.get, big)
    assert target.relocs == [(0, 5, ADDRESS), (4, 6, ADDRESS)]
    assert target.code == struct.pack('>II' if big else '<II', *WORDS)


@pytest.mark.parametrize('operand', [
    '%lo(table + (0x12340 & 0xff))', '%lo(table + (0x12340 & 0x1ffff))',
    '%lo(table + (index & 0xffff))', '%lo(other + (0x12340 & 0xffff))',
])
def test_masked_addend_rejects_unproven_expressions(operand):
    target = match.target_from_split(function(operand=operand), {'table': BASE, 'other': BASE+4}.get)
    assert target.relocs == [(0, 5, ADDRESS)]


@pytest.mark.parametrize('words', [(0x3c048002, 0x8ca44340), (0x3c048003, WORDS[1]),
                                   (WORDS[0], 0x10804340), (WORDS[0], 0x8c844344)])
def test_masked_addend_rejects_wrong_register_opcode_or_address(words):
    target = match.target_from_split(function(words), {'table': BASE}.get)
    assert target.relocs == [(0, 5, ADDRESS)]
    assert len(match.canonical_target(target, SymbolTable()).relocs) == 1


@pytest.mark.parametrize('big', [True, False])
def test_older_target_display_completion_does_not_mutate_link_target(big):
    code = struct.pack('>II' if big else '<II', *WORDS)
    target = match.RomTarget('example', 0x80001000, code, [(0, 5, ADDRESS)], big)
    symbols = SymbolTable([Symbol('table', BASE, 0x20000)])
    complete = match.RomTarget('example', target.vram, code, [(0, 5, ADDRESS), (4, 6, ADDRESS)], big)
    assert match.canonical_target(target, symbols) == match.canonical_target(complete, symbols)
    assert target.code == code
    assert target.relocs == [(0, 5, ADDRESS)]


def test_existing_low_annotation_is_preserved_even_when_different():
    target = match.RomTarget('example', 0x80001000, struct.pack('>II', *WORDS),
                             [(0, 5, ADDRESS), (4, 6, ADDRESS+4)])
    assert match._display_relocs(target) == target.relocs


def test_nonadjacent_or_clobbered_high_is_not_completed():
    for middle in (0, 0x24040000, 0x10800001):
        code = struct.pack('>III', WORDS[0], middle, WORDS[1])
        target = match.RomTarget('example', 0x80001000, code, [(0, 5, ADDRESS)])
        assert match._display_relocs(target) == target.relocs


@pytest.mark.parametrize('base,sign,addend,high,low,opcode', [
    (0x80012000, '+', 0x1d340, 0x3c048003, 0x2484f340, 'addiu'),
    (0x80048000, '-', 0x12340, 0x3c048003, 0x8c845cc0, 'lw'),
])
def test_signed_low_carry_and_negative_addends(base, sign, addend, high, low, opcode):
    fn = function((high, low), f'%lo(table {sign} (0x{addend:x} & 0xffff))')
    fn.lines[0].operands = f'$a0, %hi(table {sign} 0x{addend:x})'
    fn.lines[1].mnemonic = opcode
    address = base + (addend if sign == '+' else -addend)
    target = match.target_from_split(fn, {'table': base}.get)
    assert target.relocs == [(0, 5, address), (4, 6, address)]


def test_plain_addend_and_missing_high_annotation():
    fn = function(operand='%lo(table + 0x12340)')
    assert match.target_from_split(fn, {'table': BASE}.get).relocs == [(0, 5, ADDRESS), (4, 6, ADDRESS)]
    fn = function()
    fn.lines[0].operands = '$a0, 0x8002'
    assert match.target_from_split(fn, {'table': BASE}.get).relocs == []


def test_display_completion_preserves_exact_and_inexact_link_verdicts(tmp_path):
    import subprocess

    from decompnet import toolchains

    installed = toolchains.installed('gcc-2.7.2-kmc')
    if installed is None:
        pytest.skip('needs the registered KMC assembler')
    source = tmp_path / 'example.s'
    obj = tmp_path / 'example.o'
    source.write_text('''.set noreorder
.text
.globl example
.type example, @function
example:
lui $a0,%hi(table + 0x12340)
lw $a0,%lo(table + 0x12340)($a0)
jr $ra
nop
.size example,.-example
''')
    subprocess.run([str(toolchains.require('gcc-2.7.2-kmc')/'as'), '-EB', '-mips3', '-o', str(obj), str(source)],
                   check=True, capture_output=True, timeout=20)
    symbols = SymbolTable([Symbol('table', BASE, 0x20000)])
    code = struct.pack('>IIII', *WORDS, 0x03e00008, 0)
    for words, expected in [(code, True), (code[:-4] + b'\0\0\0\1', False)]:
        target = match.RomTarget('example', 0x80001000, words, [(0, 5, ADDRESS)])
        before = match.link(obj.read_bytes(), 'example', target, symbols, lambda *_: None)
        match.canonical_target(target, symbols)
        after = match.link(obj.read_bytes(), 'example', target, symbols, lambda *_: None)
        assert before.exact is after.exact is expected
        assert before.linked_code == after.linked_code == code
        assert target.relocs == [(0, 5, ADDRESS)]
        assert target.code == words
