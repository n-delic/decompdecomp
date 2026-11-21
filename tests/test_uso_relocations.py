import struct

import crunch64
import pytest

from decompnet import toolchains
from decompnet.compiler import compile_source
from decompnet.consoles.n64 import uso, uso_match
from decompnet.project import compiler_config

from .test_donor_reanalysis import _module


@pytest.mark.parametrize('compressed', [False, True])
def test_rebuild_uso_checks_text_and_symbol_binding(tmp_path, compressed):
    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    code = bytes.fromhex('27bdffe8 afbf0014 0c000000 00000000 8fbf0014 27bd0018 03e00008 00000000')
    relocs = struct.pack('>III', (1 << 4) | 1, 12, 1)
    cartridge = bytes(64) + _module(bytes(4) + code, compressed=compressed, relocations=relocs)
    region, = uso.read(cartridge)
    binding = uso_match.alias(region.offset, 1)
    info = {'section': region.offset, 'start': 4, 'size': len(code),
            'relocs': uso.function_relocations(region, 4, len(code))}
    assert info['relocs'] == [[8, 4, 1]]
    source = f'extern void {binding}(void); void test_entry(void) {{ {binding}(); }}'
    built = compile_source(cfg, '', source)
    assert built.ok, built.log
    assert uso_match.link(built.obj, 'test_entry', code, info).exact
    assert uso.rebuild(cartridge, [('test_entry', info, built.obj)]) == cartridge
    wrong = compile_source(cfg, '', source.replace(binding, uso_match.alias(region.offset, 2)))
    assert wrong.ok, wrong.log
    result = uso_match.link(wrong.obj, 'test_entry', code, info)
    assert result.instructions_exact and not result.exact
    assert any(binding in problem for problem in result.problems)
    with pytest.raises(ValueError, match='no longer matches'):
        uso.rebuild(cartridge, [('test_entry', info, wrong.obj)])
    with pytest.raises(ValueError, match='placement'):
        uso.rebuild(cartridge, [('test_entry', info | {'relocs': []}, built.obj)])


def test_uso_rejects_unsupported_or_duplicate_relocation_records():
    text = bytes(32)
    for records in (((0x19, 4, 1),), ((0x11, 4, 1), (0x12, 4, 1)), ((0x91, 4, 1),), ((0x11, 32, 1),)):
        payload = b''.join(struct.pack('>III', *record) for record in records)
        region, = uso.read(bytes(64) + _module(text, relocations=payload))
        assert region.data == text
        assert region.relocations is None


def test_yay0_rebuild_uses_literals_and_verifies_back_references():
    original = b'abcdefgh' * 8
    compressed = crunch64.yay0.compress(original)
    changed = b'zbcdefgh' * 8
    result = uso._yay0(compressed, changed)
    assert result != compressed
    assert uso._yay0(result) == changed
    assert uso._yay0(compressed, changed[:-1] + b'q') is None
