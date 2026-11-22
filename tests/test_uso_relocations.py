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


def test_local_overlay_calls_link_and_rebuild_with_distinct_offsets(tmp_path):
    from threading import Event

    from decompnet.app.overlay_donor_aliases import retry
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    source = 'void example(void) { placeholder(1); placeholder(2); }'
    header = 'void placeholder(int);'
    baseline = compile_source(cfg, header, source)
    assert baseline.ok, baseline.log
    body = extract_function(baseline.obj, 'example')
    assert len(body.relocs) == 2
    offsets = (0x60, 0x70)
    assert len(body.code) + 4 < min(offsets)
    code = bytearray(body.code)
    records = []
    for reloc, offset in zip(body.relocs, offsets, strict=True):
        assert reloc.type == 4
        code[reloc.offset:reloc.offset + 4] = (0x0c000000 | offset >> 2).to_bytes(4, 'big')
        records.append(struct.pack('>III', 0x11, 4 + reloc.offset, 0))
    code = bytes(code)
    text = (bytes(4) + code).ljust(0x80, b'\0')
    cartridge = bytes(64) + _module(text, relocations=b''.join(records), text_base=True)
    region, = uso.read(cartridge)
    relocs = uso.function_relocations(region, 4, len(code))
    calls = uso.function_local_calls(region, 4, len(code), relocs)
    assert calls == [[r.offset, 1, offset] for r, offset in zip(body.relocs, offsets, strict=True)]
    info = {'section': region.offset, 'start': 4, 'size': len(code), 'relocs': relocs, 'local_calls': calls}
    import json

    legacy = dict(info)
    legacy.pop('local_calls')
    row = {'id': 17, 'meta': json.dumps({'uso': legacy}), 'target': code, 'rom_offset': 256 + 4}
    banks = {str(region.offset): 256}
    updated, = uso.metadata_updates(cartridge, banks, [row])
    assert updated[0] == row['id'] and json.loads(updated[1])['uso'] == info
    assert list(uso.metadata_updates(cartridge, banks, [row | {'target': bytes(len(code))}])) == []
    assert list(uso.metadata_updates(cartridge, banks, [row | {'rom_offset': 4}])) == []

    original = uso_match.link(baseline.obj, 'example', code, info)
    assert not original.exact
    target = uso_match.symbolic_target(uso_match.Target('example', 4, code, [], uso=info))

    def measure(text, context):
        built = compile_source(cfg, context, text)
        assert built.ok, built.log
        return uso_match.link(built.obj, 'example', code, info).candidate

    rebound, context = retry(source, header, 'example', original.candidate, target, measure, Event())
    built = compile_source(cfg, context, rebound)
    assert built.ok, built.log
    linked = uso_match.link(built.obj, 'example', code, info)
    assert linked.exact and linked.linked_code == code
    assert uso.rebuild(cartridge, [('example', info, built.obj)]) == cartridge
    assert not uso_match.link(built.obj, 'example', code, info | {'local_calls': []}).exact
    wrong = compile_source(cfg, context, rebound.replace(target.relocs[0].symbol, target.relocs[1].symbol))
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'example', code, info).exact
    with pytest.raises(ValueError, match='local call metadata'):
        uso.rebuild(cartridge, [('example', info | {'local_calls': calls[:1]}, built.obj)])


@pytest.mark.parametrize('start,end', [(2, 9), (9, 2), (8190, 8193)])
def test_overlay_data_rebase_preserves_compiler_relocations_and_exact_bytes(tmp_path, start, end):
    from decompnet.app.overlay_donor_aliases import offset_retry
    from decompnet.elfobj import extract_function

    if not toolchains.installed('ido-5.3'):
        pytest.skip('needs IDO 5.3')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    binding = uso_match.alias(64, 1)
    header = '/* donor declaration */ extern int donor_data[];'
    source = f'int example(void) {{ return donor_data[{start}]; }}'
    original = compile_source(cfg, header, source)
    expected = compile_source(cfg, f'extern int {binding}[16384];',
                              f'int example(void) {{ return {binding}[{end}]; }}')
    assert original.ok, original.log
    assert expected.ok, expected.log
    a = extract_function(original.obj, 'example')
    b = extract_function(expected.obj, 'example')
    info = {'section': 64, 'relocs': [(r.offset, r.type, 1) for r in b.relocs]}
    baseline = uso_match.link(original.obj, 'example', b.code, info)
    assert not baseline.exact and a.code != b.code
    target = uso_match.symbolic_target(uso_match.Target('example', 0, b.code, [], uso=info))
    changed, context = offset_retry(source, header, 'example', baseline.candidate, target)
    rebuilt = compile_source(cfg, context, changed)
    assert rebuilt.ok, rebuilt.log
    verified = uso_match.link(rebuilt.obj, 'example', b.code, info)
    assert verified.exact
    delta = 4 * (end - start)
    assert f'+ ({delta})' in changed
    wrong = compile_source(cfg, context, changed.replace(f'+ ({delta})', f'+ ({delta + 4})'))
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'example', b.code, info).exact


@pytest.mark.parametrize('changed_offsets,shared_binding', [(False, False), (True, False), (True, True)])
def test_overlay_probes_a_c_linkage_data_declaration_in_cpp(tmp_path, changed_offsets, shared_binding):
    from threading import Event

    from decompnet.app.overlay_donor_aliases import retry
    from decompnet.elfobj import extract_function

    toolchain = 'ido-7.1-ncc'
    if not toolchains.installed(toolchain):
        pytest.skip('needs IDO 7.1 NCC')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': toolchain, 'flags': ['-O2', '-mips2', '-G', '0', '-non_shared']}})
    header = 'extern "C" { extern volatile int data_word[64]; void consume(int); }'
    donor = 'extern "C" int example() { consume(data_word[2]); return data_word[3]; }'
    section = 64
    first, second, call = (uso_match.alias(section, i) for i in (1, 2, 3))
    if shared_binding:
        second = first
        donor = donor.replace('data_word', first)
        header = header.replace('data_word', first)
    baseline = compile_source(cfg, header, donor)
    assert baseline.ok, baseline.log
    expected_header = f'extern "C" {{ extern volatile int {first}[64]; extern volatile int {second}[64]; void {call}(int); }}'
    i, j = (9, 11) if changed_offsets else (2, 3)
    expected_c = f'extern "C" int example() {{ {call}({first}[{i}]); return {second}[{j}]; }}'
    expected = compile_source(cfg, expected_header, expected_c)
    assert expected.ok, expected.log
    wanted = extract_function(expected.obj, 'example')
    indices = {second: 2, first: 1, call: 3}
    info = {'section': section, 'relocs': [[r.offset, r.type, indices[r.symbol]] for r in wanted.relocs]}
    original = uso_match.link(baseline.obj, 'example', wanted.code, info)
    assert not original.exact
    target = uso_match.symbolic_target(uso_match.Target('example', 0, wanted.code, [], uso=info))

    def measure(text, context):
        built = compile_source(cfg, context, text)
        assert built.ok, built.log
        return uso_match.link(built.obj, 'example', wanted.code, info).candidate

    source, header = retry(donor, header, 'example', original.candidate, target, measure, Event())
    built = compile_source(cfg, header, source)
    assert built.ok, built.log
    assert uso_match.link(built.obj, 'example', wanted.code, info).exact
    assert 'decomp_donor_probe_' not in source + header
    if changed_offsets:
        assert '+ (28)' in source
        wrong = compile_source(cfg, header, source.replace('+ (28)', '+ (24)'))
        assert wrong.ok, wrong.log
        assert not uso_match.link(wrong.obj, 'example', wanted.code, info).exact


def test_donor_local_text_calls_bind_to_an_independently_linked_overlay(tmp_path):
    from decompnet.app.overlay_donor_aliases import local_functions
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-7.1') is None:
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    header = """
        extern volatile int counter;
        static void helper_a(int x) { counter = x; }
        static void helper_b(int x) { counter += x; }
    """
    source = 'void entry(int x) { helper_a(x); helper_b(x + 1); }'
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    body = extract_function(original.obj, 'entry')
    assert [(r.type, r.symbol) for r in body.relocs] == [(4, 'helper_a'), (4, 'helper_b')]
    offsets = (0x80, 0xA0)
    code = bytearray(body.code)
    calls = []
    for reloc, offset in zip(body.relocs, offsets, strict=True):
        code[reloc.offset:reloc.offset + 4] = (0x0c000000 | offset >> 2).to_bytes(4, 'big')
        calls.append([reloc.offset, 1, offset])
    info = {'section': 32, 'start': 0, 'size': len(code), 'relocs': [[at, 4, index] for at, index, _ in calls],
            'local_calls': calls}
    expected = uso_match.symbolic_target(uso_match.Target('entry', 0, bytes(code), [], uso=info))
    initial = uso_match.link(original.obj, 'entry', bytes(code), info)
    assert not initial.exact
    assert [(r.type, r.symbol) for r in initial.candidate.relocs] == [(4, '.text'), (4, '.text')]
    assert [r.addend for r in initial.candidate.relocs] != [offset // 4 for offset in offsets]
    rebound, context = local_functions(source, header, 'entry', original.obj, initial.candidate, expected)
    assert 'static' not in context and 'counter' not in context
    for reloc in expected.relocs:
        assert reloc.symbol in context and reloc.symbol in rebound
    compiled = compile_source(cfg, context, rebound)
    assert compiled.ok, compiled.log
    assert uso_match.link(compiled.obj, 'entry', bytes(code), info).exact
    first, second = (r.symbol for r in expected.relocs)
    wrong = compile_source(cfg, context, rebound.replace(second, first))
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'entry', bytes(code), info).exact


def test_recovers_loader_metadata_for_raw_functions_with_an_existing_runtime_address(tmp_path):
    import json

    from decompnet import consoles

    code = bytes.fromhex('27bdffe8 afbf0014 0c000000 00000000 8fbf0014 27bd0018 03e00008 00000000')
    records = struct.pack('>III', 0x11, 12, 1)
    cartridge = bytes(64) + _module(bytes(4) + code, compressed=False, relocations=records)
    region, = uso.read(cartridge)
    row = {'id': 1, 'name': 'entry', 'vram': 0x80010000, 'rom_offset': region.offset + 4,
           'size': len(code), 'target': code, 'relocs': '[]', 'meta': None}
    good, = uso.metadata_updates(cartridge, {}, [row])
    assert good[0] == 1
    info = json.loads(good[1])['uso']
    assert info == {'section': region.offset, 'start': 4, 'size': len(code), 'relocs': [[8, 4, 1]], 'native': True}
    assert not list(uso.metadata_updates(cartridge, {}, [row | {'rom_offset': region.offset},
                                                         row | {'target': b'\0' * len(code)}]))
    compressed = bytes(64) + _module(bytes(4) + code, compressed=True, relocations=records)
    assert not list(uso.metadata_updates(compressed, {}, [row]))

    cfg = {'platform': 'n64'}
    console = consoles.of(cfg)
    recovered = row | {'meta': good[1]}
    target = console.target_from_row(recovered, cfg)
    assert target.vram == row['vram'] and target.code == code and console.export_overlay(target) == info
    misplaced = recovered | {'rom_offset': row['rom_offset'] + 4}
    assert console.export_overlay(console.target_from_row(misplaced, cfg)) is None

    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3')
    compiler = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    alias = uso_match.alias(region.offset, 1)
    source = f'extern void {alias}(void); void entry(void) {{ {alias}(); }}'
    built = compile_source(compiler, '', source)
    assert built.ok, built.log
    assert console.link_function(built.obj, 'entry', target, None, None).exact
    assert console.rebuild_overlays(cartridge, [('entry', info, built.obj)]) == cartridge



def test_split_keeps_a_raw_overlay_function_identity_and_records_its_loader_relocations(tmp_path):
    from decompnet.rom.split import SplitResult, function_from_bytes

    code = bytes.fromhex('27bdffe8 afbf0014 0c000000 00000000 8fbf0014 27bd0018 03e00008 00000000')
    records = struct.pack('>III', 0x11, 12, 1)
    cartridge = bytes(64) + _module(bytes(4) + code, compressed=False, relocations=records)
    region, = uso.read(cartridge)
    fn = function_from_bytes('entry', code, region.offset + 4, 0x80010000, 'main')
    result = SplitResult([fn], [], [], {}, tmp_path / 'game.yaml', 'IDO', '', image='game.z64')
    (tmp_path / result.image).write_bytes(cartridge)
    uso.add_to_split(result, tmp_path, cartridge, lambda *_: None)
    assert result.functions == [fn]
    assert (fn.name, fn.vram, fn.rom) == ('entry', 0x80010000, region.offset + 4)
    assert fn.uso['native'] is True and fn.uso['relocs'] == [[8, 4, 1]]
