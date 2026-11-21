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
    from types import SimpleNamespace

    from decompnet.app.donor_relocations import overlay_names
    from decompnet.app.source_symbol_recovery import rename
    from decompnet.app.twins import Print

    target = uso_match.Target('test_entry', 4, code, [], uso=info)
    net = SimpleNamespace(console=lambda _: SimpleNamespace(export_overlay=lambda t: t.uso),
                          _rom_target=lambda _: target,
                          _target_obj=lambda *_: uso_match.symbolic_target(target))
    wrong_symbol = uso_match.alias(region.offset, 2)
    donor = Print('fingerprint', [wrong_symbol], len(code), layout=[(8, 4)])
    mapping = overlay_names(net, {}, {'name': 'test_entry'}, donor, 'original_entry')
    assert mapping == {'original_entry': 'test_entry', wrong_symbol: binding}
    donor_source = source.replace(binding, wrong_symbol).replace('test_entry', 'original_entry')
    rebound = compile_source(cfg, '', rename(donor_source, mapping))
    assert rebound.ok, rebound.log
    assert uso_match.link(rebound.obj, 'test_entry', code, info).exact
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


@pytest.mark.parametrize("cpp", [False, True])
def test_overlay_replays_distinct_loader_calls_from_one_donor_placeholder(tmp_path, cpp):
    import threading
    from unittest.mock import Mock

    from decompnet.app.overlay_donor_aliases import retry

    toolchain = 'ido-7.1-ncc' if cpp else 'ido-5.3'
    if toolchains.installed(toolchain) is None:
        pytest.skip(f'needs {toolchain}')
    flags = ['-O2', '-G', '0', '-non_shared', '-mips2', '-32'] if cpp else toolchains.get(toolchain).flags
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': toolchain, 'flags': flags}})
    source = 'void example(void) { placeholder(1); placeholder(2); }'
    header = 'extern void placeholder(int);'
    if cpp:
        header = 'extern "C" { void placeholder(int); }\ninline void first_call() { placeholder(1); }'
        source = 'extern "C" void example() { first_call(); placeholder(2); }'
    section = 64
    first, second = uso_match.alias(section, 1), uso_match.alias(section, 2)
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    from decompnet.elfobj import extract_function
    raw = extract_function(original.obj, 'example')
    sites = [(r.offset, r.type, i) for i, r in enumerate(raw.relocs, 1)]
    assert len(sites) == 2
    info = {'section': section, 'start': 0, 'size': len(raw.code), 'relocs': sites}
    target = uso_match.symbolic_target(uso_match.Target('example', 0, raw.code, [], uso=info))
    baseline = uso_match.link(original.obj, 'example', raw.code, info)
    assert baseline.instructions_exact and not baseline.exact

    def measure(text, context):
        build = compile_source(cfg, context, text)
        assert build.ok, build.log
        return uso_match.link(build.obj, 'example', raw.code, info).candidate

    stopped = threading.Event()
    stopped.set()
    forbidden = Mock(side_effect=AssertionError('stopped probe ran'))
    assert retry(source, header, 'example', baseline.candidate, target, forbidden, stopped) is None
    rewritten, context = retry(source, header, 'example', baseline.candidate, target, measure, threading.Event())
    assert f'{first}(1)' in (context if cpp else rewritten) and f'{second}(2)' in rewritten
    result = compile_source(cfg, context, rewritten)
    assert result.ok, result.log
    assert uso_match.link(result.obj, 'example', raw.code, info).exact
