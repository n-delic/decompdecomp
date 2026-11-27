import struct

import crunch64
import pytest

from decompnet import toolchains
from decompnet.compiler import compile_source
from decompnet.consoles.n64 import uso, uso_match
from decompnet.project import compiler_config
from decompnet.rom.verifier_link import link_overlay

from .test_donor_reanalysis import _module

_python_link = uso_match.link


def audit_overlay(exchange, obj, name, target, info):
    token = uso_match.overlay_linker.set(None)
    try:
        expected = _python_link(obj, name, target, info)
    finally:
        uso_match.overlay_linker.reset(token)
    actual = link_overlay(obj, name, target, info, exchange)
    assert actual is not None, "Rust declined the overlay compiler object"
    assert actual == expected, (actual, expected)
    return actual


@pytest.fixture(autouse=True)
def rust_overlay_parity(monkeypatch):
    """Run the existing real compiler cases through the Rust linker whenever it is built."""
    from decompnet.app import mcp_gateway
    from decompnet.rom.desktop_judge import RustJudge
    from decompnet.rom.verifier_link import overlay_linker

    if not (program := mcp_gateway.built()):
        yield
        return
    monkeypatch.setenv("DECOMP_MCP_GATEWAY", str(program))
    judge = RustJudge()
    token = overlay_linker.set(lambda *args: audit_overlay(judge._exchange, *args))
    try:
        yield
    finally:
        overlay_linker.reset(token)
        judge.close()


@pytest.mark.parametrize('compressed', [False, True])
def test_rebuild_uso_checks_text_and_symbol_binding(tmp_path, compressed, monkeypatch):
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
    unverified = uso_match.link(built.obj, 'test_entry', code, {'section': None, 'relocs': None})
    assert unverified.instructions_exact and not unverified.exact
    assert 'validated relocation metadata' in unverified.problems[0]
    missing = uso_match.link(built.obj, 'test_entry', code, info | {'relocs': info['relocs'] + [[4, 6, 3], [0, 5, 4]]})
    assert missing.instructions_exact and not missing.exact
    assert missing.problems == [f'Overlay relocation at +0x{at:X} requires {uso_match.alias(region.offset, index)}.'
                                for at, index in [(0, 4), (4, 3)]]
    assert uso.rebuild(cartridge, [('test_entry', info, built.obj)]) == cartridge
    from decompnet.app import mcp_gateway
    from decompnet.rom.desktop_judge import RustJudge

    if mcp_gateway.built():
        calls = []
        def audited_overlay(judge, *args):
            result = audit_overlay(judge._exchange, *args)
            calls.append(result.exact)
            return result

        with monkeypatch.context() as audit:
            audit.setattr(RustJudge, "link_overlay", audited_overlay)
            token = uso_match.overlay_linker.set(None)
            try:
                assert uso.rebuild(cartridge, [('test_entry', info, built.obj)]) == cartridge
                assert calls == [True]
            finally:
                uso_match.overlay_linker.reset(token)
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


def test_overlay_jump_table_allows_unrelated_arithmetic_in_delay_slot():
    from .test_mips_indirect_audit import fixture

    image, _ = fixture()
    code = bytearray(image[0x1000:0x103C])
    code[8:12] = (0x306500F0).to_bytes(4, 'big')  # andi a1, v1, 0xf0
    code[16:20] = (0x3C010000).to_bytes(4, 'big')
    code[24:28] = (0x8C220020).to_bytes(4, 'big')
    rodata = bytes(0x20) + struct.pack('>II', 36, 44)
    region = uso.Text('table', 64, bytes(code), False, rodata=rodata, rodata_bases=(1,),
                      text_bases=(2,), rodata_relocations=((0x20, 2, 2), (0x24, 2, 2)))
    relocs = [[16, 5, 1], [24, 6, 1]]
    assert uso.function_jump_tables(region, 0, len(code), relocs) == [[24, 1, [36, 44]]]
    code[8:12] = (0x306300F0).to_bytes(4, 'big')  # overwrites the table index
    from dataclasses import replace

    assert not uso.function_jump_tables(replace(region, data=bytes(code)), 0, len(code), relocs)


def test_overlay_defined_zero_data_requires_explicit_loader_binding(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from inspect import unwrap
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import Mock

    from decompnet import consoles
    from decompnet.app import donor_build, exact_match_inputs
    from decompnet.app.overlay_donor_aliases import defined_data_bindings, globalize_zero_data
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    header = 'static float values[2]; static float other;'
    source = 'float example(void) { return values[0] + values[1] + other; }'
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    body = extract_function(original.obj, 'example')
    assert body and {r.type for r in body.relocs} == {5, 6}
    assert {r.symbol for r in body.relocs} == {'.bss'}
    records = b''.join(struct.pack('>III', 0x13 if r.type == 5 else 0x12, r.offset + 4, 1)
                       for r in body.relocs)
    cartridge = bytes(64) + _module(bytes(4) + body.code, relocations=records)
    region, = uso.read(cartridge)
    info = {'section': region.offset, 'start': 4, 'size': len(body.code),
            'relocs': uso.function_relocations(region, 4, len(body.code))}
    target = uso_match.symbolic_target(uso_match.Target('example', 4, body.code, [], uso=info))
    initial = uso_match.link(original.obj, 'example', body.code, info)
    assert initial.instructions_exact and not initial.exact
    context, names = globalize_zero_data(header, initial.candidate, target)
    assert names == {'values', 'other'}
    globalized = compile_source(cfg, context, source)
    assert globalized.ok, globalized.log
    candidate = uso_match.link(globalized.obj, 'example', body.code, info).candidate
    rebound, context = defined_data_bindings(source, context, candidate, target, names,
                                             lambda aliases: aliases, uso_match.data_reference)
    built = compile_source(cfg, context, rebound)
    assert built.ok, built.log
    assert uso_match.link(built.obj, 'example', body.code, info).exact
    assert uso.rebuild(cartridge, [('example', info, built.obj)]) == cartridge
    console = consoles.of({'platform': 'n64'})
    placement = uso_match.Target('example', 4, body.code, [], uso=info)
    record = Mock()
    net = SimpleNamespace(
        _compile=lambda _project, h, s, _fn: compile_source(cfg, h, s),
        _evaluate=lambda _fn, _project, obj: (lambda result: (result.candidate, result.exact, result.problems))(
            uso_match.link(obj, 'example', body.code, info)),
        _rom_target=lambda _fn: placement, _target_obj=lambda *_: target,
        console=lambda _: console, _record=record,
        progress=SimpleNamespace(judging=lambda *_args, **_kwargs: nullcontext()))
    monkeypatch.setattr(exact_match_inputs, 'capture', lambda *_: {})
    monkeypatch.setattr(donor_build, 'acceptable', lambda *_: True)
    fn = {'name': 'example', 'size': len(body.code), 'id': 1}
    accepted, _ = unwrap(donor_build._try)(net, {'slug': 'test', 'arch': 'mips'}, {}, fn, source, header,
                                          SimpleNamespace(id=1), SimpleNamespace(stop=Event()))
    assert accepted
    saved = record.call_args.kwargs['exact_inputs']
    replayed = compile_source(cfg, saved['header'], saved['source'])
    assert replayed.ok, replayed.log
    assert uso_match.link(replayed.obj, 'example', body.code, info).exact
    assert '_data_8__ref' in context
    for wrong_context in (context.replace('_data_8__ref', '_data_4__ref'),
                          context.replace('_sym_1_data_', '_sym_2_data_'),
                          context.replace('[2];', '[2] = {1};', 1)):
        wrong_source = rebound
        if '_data_4__ref' in wrong_context:
            wrong_source = rebound.replace('_data_8__ref', '_data_4__ref')
        if '_sym_2_data_' in wrong_context:
            wrong_source = wrong_source.replace('_sym_1_data_', '_sym_2_data_')
        wrong = compile_source(cfg, wrong_context, wrong_source)
        assert wrong.ok, wrong.log
        assert not uso_match.link(wrong.obj, 'example', body.code, info).exact


def test_overlay_explicit_binding_preserves_c_donor_expressions(tmp_path):
    from decompnet.app.overlay_donor_aliases import explicit_symbol_bindings
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    header = 'extern int donor_data;'
    source = 'int example(void) { return donor_data + 1; }'
    built = compile_source(cfg, header, source)
    assert built.ok, built.log
    body = extract_function(built.obj, 'example')
    info = {'section': 64, 'start': 0, 'size': len(body.code),
            'relocs': [(r.offset, r.type, 1) for r in body.relocs]}
    target = uso_match.symbolic_target(uso_match.Target('example', 0, body.code, [], uso=info))
    baseline = uso_match.link(built.obj, 'example', body.code, info)
    assert baseline.instructions_exact and not baseline.exact
    rebound, context = explicit_symbol_bindings(source, header, baseline.candidate, target,
                                               uso_match.separate_reference)
    assert rebound == source
    result = compile_source(cfg, context, rebound)
    assert result.ok, result.log
    assert uso_match.link(result.obj, 'example', body.code, info).exact


def test_overlay_plain_struct_data_uses_explicit_defined_binding(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from inspect import unwrap
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import Mock

    from decompnet import consoles
    from decompnet.app import donor_build, exact_match_inputs
    from decompnet.app.overlay_donor_aliases import globalize_zero_data
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    header = ('typedef float Real; typedef struct { int tag; Real x; Real y; } State; '
              'static State shared;')
    source = 'float example(void) { return shared.x + shared.y; }'
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    body = extract_function(original.obj, 'example')
    assert {r.symbol for r in body.relocs} == {'.bss'}
    records = b''.join(struct.pack('>III', 0x13 if r.type == 5 else 0x12, r.offset + 4, 1)
                       for r in body.relocs)
    cartridge = bytes(64) + _module(bytes(4) + body.code, relocations=records)
    region, = uso.read(cartridge)
    info = {'section': region.offset, 'start': 4, 'size': len(body.code),
            'relocs': uso.function_relocations(region, 4, len(body.code))}
    placement = uso_match.Target('example', 4, body.code, [], uso=info)
    target = uso_match.symbolic_target(placement)
    initial = uso_match.link(original.obj, 'example', body.code, info)
    unsafe = (header + ' typedef struct { int value; Bad() {} } Bad; static Bad unsafe;\n'
              '#if UNKNOWN\ntypedef int Maybe; static Maybe hidden;\n#endif\n'
              'typedef struct { int x; } Aliased;\n#define Aliased Bad\nstatic Aliased shadow;\n')
    _, names = globalize_zero_data(unsafe, initial.candidate, target)
    assert names == {'shared'}
    console = consoles.of({'platform': 'n64'})
    record = Mock()
    net = SimpleNamespace(
        _compile=lambda _project, h, s, _fn: compile_source(cfg, h, s),
        _evaluate=lambda _fn, _project, obj: (lambda result: (result.candidate, result.exact, result.problems))(
            uso_match.link(obj, 'example', body.code, info)),
        _rom_target=lambda _fn: placement, _target_obj=lambda *_: target,
        console=lambda _: console, _record=record,
        progress=SimpleNamespace(judging=lambda *_args, **_kwargs: nullcontext()))
    monkeypatch.setattr(exact_match_inputs, 'capture', lambda *_: {})
    monkeypatch.setattr(donor_build, 'acceptable', lambda *_: True)
    fn = {'name': 'example', 'size': len(body.code), 'id': 1}
    accepted, _ = unwrap(donor_build._try)(net, {'slug': 'test', 'arch': 'mips'}, {}, fn, source, header,
                                          SimpleNamespace(id=1), SimpleNamespace(stop=Event()))
    assert accepted
    saved = record.call_args.kwargs['exact_inputs']
    assert '_data_0__ref' in saved['header']
    result = compile_source(cfg, saved['header'], saved['source'])
    assert result.ok, result.log
    assert uso_match.link(result.obj, 'example', body.code, info).exact
    assert uso.rebuild(cartridge, [('example', info, result.obj)]) == cartridge


def test_overlay_cpp_method_calls_keep_loader_binding(tmp_path):
    from decompnet.app.overlay_donor_aliases import cpp_methods, explicit_symbol_bindings
    from decompnet.elfobj import extract_function, unmangle

    if toolchains.installed('ido-7.1-ncc') is None:
        pytest.skip('needs IDO 7.1 C++')
    flags = ['-O2', '-G', '0', '-non_shared', '-mips2', '-32']
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-7.1-ncc', 'flags': flags}})
    header = 'class Mesh { public: int example(int); int adjacent(int); };'
    source = 'int Mesh::example(int i) { return adjacent(i) + adjacent(i + 1); }'
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    raw = extract_function(unmangle(original.obj, lambda name: name == 'example'), 'example')
    assert len(raw.relocs) == 2
    info = {'section': 64, 'start': 0, 'size': len(raw.code),
            'relocs': [(r.offset, r.type, 1) for r in raw.relocs]}
    target = uso_match.symbolic_target(uso_match.Target('example', 0, raw.code, [], uso=info))
    baseline = uso_match.link(original.obj, 'example', raw.code, info)
    assert baseline.instructions_exact and not baseline.exact
    marker_source, marker_header = explicit_symbol_bindings(source, header, baseline.candidate, target,
                                                           uso_match.separate_reference)
    marker_build = compile_source(cfg, marker_header, marker_source)
    assert marker_build.ok, marker_build.log
    assert uso_match.link(marker_build.obj, 'example', raw.code, info).exact
    rebound, context = cpp_methods(source, header, 'example', baseline.candidate, target,
                                   uso_match.separate_reference)
    assert rebound.count('(this, ') == 2
    built = compile_source(cfg, context, rebound)
    assert built.ok, built.log
    assert uso_match.link(built.obj, 'example', raw.code, info).exact
    wrong = compile_source(cfg, context.replace('sym_1__ref1', 'sym_2__ref1'),
                           rebound.replace('sym_1__ref1', 'sym_2__ref1'))
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'example', raw.code, info).exact
    info['relocs'][1] = (*info['relocs'][1][:2], 2)
    split = uso_match.symbolic_target(uso_match.Target('example', 0, raw.code, [], uso=info))
    assert cpp_methods(source, header, 'example', baseline.candidate, split, uso_match.separate_reference) is None


@pytest.mark.parametrize('vtable_offset', [0, 0x17FFF, 0x18000, -4])
def test_overlay_cpp_generated_symbols_use_explicit_object_bindings(tmp_path, monkeypatch, vtable_offset):
    from contextlib import nullcontext
    from inspect import unwrap
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import Mock

    from decompnet import consoles
    from decompnet.app import donor_build, exact_match_inputs
    from decompnet.app.overlay_donor_aliases import explicit_symbol_bindings
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-7.1-ncc') is None:
        pytest.skip('needs IDO 7.1 C++')
    flags = ['-O2', '-G', '0', '-non_shared', '-mips2', '-32']
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-7.1-ncc', 'flags': flags}})
    header = ('struct Base { Base(int); }; '
              'struct Child : Base { Child() : Base(3) {} virtual void f(); }; '
              'extern "C" void sink(void *);')
    source = 'extern "C" void example() { Child c; sink(&c); }'
    donor = compile_source(cfg, header, source)
    assert donor.ok, donor.log
    body = extract_function(donor.obj, 'example')
    indices = {'__ct__4BaseFi': 1, '__vtbl__5Child': 2, 'sink': 3}
    assert {r.symbol for r in body.relocs} == indices.keys()
    kinds = {4: 0x11, 5: 0x13, 6: 0x12}
    records = b''.join(struct.pack('>III', (indices[r.symbol] << 4) | (kinds[r.type] & 15), r.offset + 4, 1)
                       for r in body.relocs)
    target_code = bytearray(body.code)
    for reloc in body.relocs:
        if reloc.symbol == '__vtbl__5Child':
            value = ((vtable_offset + 0x8000) >> 16) & 0xFFFF if reloc.type == 5 else vtable_offset & 0xFFFF
            target_code[reloc.offset + 2:reloc.offset + 4] = value.to_bytes(2, 'big')
    target_code = bytes(target_code)
    cartridge = bytes(64) + _module(bytes(4) + target_code, relocations=records)
    region, = uso.read(cartridge)
    info = {'section': region.offset, 'start': 4, 'size': len(body.code),
            'relocs': uso.function_relocations(region, 4, len(body.code))}
    target = uso_match.symbolic_target(uso_match.Target('example', 4, target_code, [], uso=info))
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    baseline = uso_match.link(original.obj, 'example', target_code, info)
    assert not baseline.exact
    rebound, context = explicit_symbol_bindings(source, header, baseline.candidate, target, uso_match.separate_reference)
    built = compile_source(cfg, context, rebound)
    assert built.ok, built.log
    assert uso_match.link(built.obj, 'example', target_code, info).exact
    assert uso.rebuild(cartridge, [('example', info, built.obj)]) == cartridge
    wrong = compile_source(cfg, context.replace('_sym_1__ref1', '_sym_2__ref1'), rebound)
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'example', target_code, info).exact
    wrong_context = context.replace('decomp_overlay_bind___ct__4BaseFi = ',
                                    'decomp_overlay_bind___ct__4BaseFi = 1 + ')
    wrong = compile_source(cfg, wrong_context, rebound)
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'example', target_code, info).exact
    vtable_line = next(line for line in context.splitlines() if 'void *decomp_overlay_bind___vtbl__' in line)
    wrong_context = context.replace(vtable_line, vtable_line.removesuffix(';') + ' + 1;')
    wrong = compile_source(cfg, wrong_context, rebound)
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'example', target_code, info).exact

    console = consoles.of({'platform': 'n64'})
    record = Mock()
    net = SimpleNamespace(
        _compile=lambda _project, h, s, _fn: compile_source(cfg, h, s),
        _evaluate=lambda _fn, _project, obj: (lambda result: (result.candidate, result.exact, result.problems))(
            uso_match.link(obj, 'example', target_code, info)),
        _rom_target=lambda _fn: uso_match.Target('example', 4, target_code, [], uso=info),
        _target_obj=lambda *_: target, console=lambda _: console, _record=record,
        progress=SimpleNamespace(judging=lambda *_args, **_kwargs: nullcontext()))
    monkeypatch.setattr(exact_match_inputs, 'capture', lambda *_: {})
    monkeypatch.setattr(donor_build, 'acceptable', lambda *_: True)
    fn = {'name': 'example', 'size': len(body.code), 'id': 1}
    accepted, _ = unwrap(donor_build._try)(net, {'slug': 'test', 'arch': 'mips'}, {}, fn, source, header,
                                          SimpleNamespace(id=1), SimpleNamespace(stop=Event()))
    assert accepted
    saved = record.call_args.kwargs['exact_inputs']
    replayed = compile_source(cfg, saved['header'], saved['source'])
    assert replayed.ok, replayed.log
    assert uso_match.link(replayed.obj, 'example', target_code, info).exact


def test_overlay_constructor_entry_is_verified_and_rebuilt(tmp_path, monkeypatch):
    import json
    from contextlib import nullcontext
    from inspect import unwrap
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import Mock

    from decompnet import consoles
    from decompnet.app import donor_build, donor_unit, exact_match_inputs
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-7.1-ncc') is None:
        pytest.skip('needs IDO 7.1 C++')
    flags = ['-O2', '-G', '0', '-non_shared', '-mips2', '-32']
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-7.1-ncc', 'flags': flags}})
    prefix = 'struct Obj { int n; Obj(int); };\n'
    body = 'Obj::Obj(int value) { n=value; }\n'
    path = tmp_path / 'donor.cpp'
    path.write_text(prefix + body)
    invocation = SimpleNamespace(cmd=[], source_extension='.cpp')
    monkeypatch.setattr(donor_unit, '_preprocess', lambda *_args, **_kwargs: (prefix + body, None))
    source, header = donor_unit.replacement({'root': str(tmp_path)}, {'name': 'donor_ctor', 'meta': '{}'},
                                            'example', invocation, path, {})
    assert 'Obj::Obj' in source and 'struct Obj' in header
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    raw = extract_function(original.obj, '__ct__3ObjFi')
    assert raw and all(r.type == 4 for r in raw.relocs)
    records = b''.join(struct.pack('>III', 0x11, r.offset + 4, 1) for r in raw.relocs)
    cartridge = bytes(64) + _module(bytes(4) + raw.code, relocations=records)
    region, = uso.read(cartridge)
    info = {'section': region.offset, 'start': 4, 'size': len(raw.code),
            'relocs': uso.function_relocations(region, 4, len(raw.code))}
    placement = uso_match.Target('example', 4, raw.code, [], uso=info)
    target = uso_match.symbolic_target(placement)
    assert uso_match.link(original.obj, 'example', raw.code, info).candidate is None
    console = consoles.of({'platform': 'n64'})
    record = Mock()
    net = SimpleNamespace(
        _compile=lambda _project, h, s, _fn: compile_source(cfg, h, s),
        _evaluate=lambda trial_fn, _project, obj: (lambda result: (result.candidate, result.exact, result.problems))(
            uso_match.link(obj, trial_fn['name'], raw.code, info)),
        _rom_target=lambda _fn: placement, _target_obj=lambda *_: target,
        console=lambda _: console, _record=record,
        progress=SimpleNamespace(judging=lambda *_args, **_kwargs: nullcontext()))
    monkeypatch.setattr(exact_match_inputs, 'capture', lambda *_: {})
    fn = {'name': 'example', 'size': len(raw.code), 'id': 1, 'meta': json.dumps({'uso': info})}
    accepted, _ = unwrap(donor_build._try)(net, {'slug': 'test', 'arch': 'mips'}, {}, fn, source, header,
                                          SimpleNamespace(id=1), SimpleNamespace(stop=Event()))
    assert accepted
    saved = record.call_args.kwargs['exact_inputs']
    assert 'Obj::Obj' in saved['source'] and 'decomp_overlay_entry' in saved['header']
    compiled = compile_source(cfg, saved['header'], saved['source'])
    assert compiled.ok, compiled.log
    assert uso_match.link(compiled.obj, 'example', raw.code, info).exact
    assert uso.rebuild(cartridge, [('example', info, compiled.obj)]) == cartridge
    assert uso_match.link(compiled.obj, 'renamed_example', raw.code, info).exact
    named = compile_source(cfg, saved['header'].replace('decomp_overlay_entry', 'decomp_overlay_entry_original'),
                           saved['source'])
    assert named.ok, named.log
    assert uso_match.link(named.obj, 'renamed_example', raw.code, info).exact
    for bad_header in (saved['header'].replace('decomp_overlay_entry', 'unrelated_entry'),
                       saved['header'].replace('decomp_overlay_entry = ',
                                               'decomp_overlay_entry = 1 + '),
                       saved['header'].replace('__ct__3ObjFi', '__ct__3ObjFv')):
        bad = compile_source(cfg, bad_header, saved['source'])
        assert bad.ok, bad.log
        assert not uso_match.link(bad.obj, 'example', raw.code, info).exact
    changed = compile_source(cfg, saved['header'], saved['source'].replace('n=value;', 'n=value+1;'))
    assert changed.ok, changed.log
    assert not uso_match.link(changed.obj, 'example', raw.code, info).exact


def test_yay0_rebuild_uses_literals_and_verifies_back_references():
    original = b'abcdefgh' * 8
    compressed = crunch64.yay0.compress(original)
    changed = b'zbcdefgh' * 8
    result = uso._yay0(compressed, changed)
    assert result != compressed
    assert uso._yay0(result) == changed
    assert uso._yay0(compressed, changed[:-1] + b'q') is None


@pytest.mark.parametrize("cpp", [False, True, 'direct'])
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
        if cpp == 'direct':
            header = 'extern "C" void placeholder(int);\ninline void first_call() { placeholder(1); }'
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


def test_overlay_call_probes_do_not_guess_anonymous_pool_bindings(tmp_path):
    from threading import Event

    from elftools.elf.elffile import ELFFile

    from decompnet.app.overlay_donor_aliases import retry
    from decompnet.candidate_work import elf as candidate_elf
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    header = 'extern void placeholder(int);'
    source = 'void example(float *out) { placeholder(1); placeholder(2); *out=1.2345f; }'
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    body = extract_function(original.obj, 'example')
    calls = [r for r in body.relocs if r.symbol == 'placeholder']
    pool = [r for r in body.relocs if r.symbol == '.rodata']
    assert len(calls) == len(pool) == 2
    low, = [r for r in pool if r.type == 6]
    target_code = bytearray(body.code)
    target_code[low.offset + 2:low.offset + 4] = (0x20).to_bytes(2, 'big')
    target_code = bytes(target_code)
    indices = {calls[0].offset: 1, calls[1].offset: 2, **{r.offset: 3 for r in pool}}
    info = {'section': 64, 'start': 0, 'size': len(body.code),
            'relocs': [[r.offset, r.type, indices[r.offset]] for r in body.relocs]}
    target = uso_match.symbolic_target(uso_match.Target('example', 0, target_code, [], uso=info))
    initial = uso_match.link(original.obj, 'example', target_code, info)

    def measure(text, context):
        trial = compile_source(cfg, context, text)
        assert trial.ok, trial.log
        return uso_match.link(trial.obj, 'example', target_code, info).candidate

    rebound, context = retry(source, header, 'example', initial.candidate, target, measure, Event())
    assert uso_match.alias(64, 1) in rebound and uso_match.alias(64, 2) in rebound
    result = compile_source(cfg, context, rebound)
    assert result.ok, result.log
    assert not uso_match.link(result.obj, 'example', target_code, info).exact
    rodata = candidate_elf(result.obj, ELFFile).get_section_by_name('.rodata').data()
    proof = info | {'pool_loads': [[low.offset, 3, rodata[low.addend:low.addend + 4].hex()]]}
    assert uso_match.link(result.obj, 'example', target_code, proof).exact


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


@pytest.mark.parametrize('preserve', [False, True])
def test_donor_local_text_calls_bind_to_an_independently_linked_overlay(tmp_path, preserve, monkeypatch):
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
    if preserve:
        header = 'int unrelated(a, b)\nint a; int b;\n{ return a + b; }\n' + header
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
    rebound, context = local_functions(source, header, 'entry', original.obj, initial.candidate, expected,
                                       preserve_context=preserve)
    if preserve:
        assert 'int unrelated(a, b)\nint a; int b;' in context and 'return a + b;' in context
    else:
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
    if preserve:
        from contextlib import nullcontext
        from inspect import unwrap
        from threading import Event
        from types import SimpleNamespace
        from unittest.mock import Mock

        from decompnet import consoles
        from decompnet.app import donor_build, donor_unit, exact_match_inputs

        compact_header = donor_unit.DONOR_UNIT_HEADER + '''
            extern volatile int counter;
            static void helper_a(int x) { counter = x; }
            static void helper_b(int x) { counter += x; }
        '''
        forced = []
        def compile_trial(_project, h, s, _fn):
            if 'uso_' in s and 'counter' in h:
                forced.append(True)
                return SimpleNamespace(ok=False, log='forced full-context failure')
            return compile_source(cfg, h, s)

        record = Mock()
        placement = uso_match.Target('entry', 0, bytes(code), [], uso=info)
        net = SimpleNamespace(
            _compile=compile_trial,
            _evaluate=lambda _fn, _project, obj: (lambda result: (result.candidate, result.exact, result.problems))(
                uso_match.link(obj, 'entry', bytes(code), info)),
            _rom_target=lambda _fn: placement, _target_obj=lambda *_: expected,
            console=lambda _: consoles.of({'platform': 'n64'}), _record=record,
            progress=SimpleNamespace(judging=lambda *_args, **_kwargs: nullcontext()))
        monkeypatch.setattr(exact_match_inputs, 'capture', lambda *_: {})
        monkeypatch.setattr(donor_build, 'acceptable', lambda *_: True)
        fn = {'name': 'entry', 'size': len(code), 'id': 1}
        accepted, _ = unwrap(donor_build._try)(net, {'slug': 'test', 'arch': 'mips'}, {}, fn, source,
                                              compact_header, SimpleNamespace(id=1), SimpleNamespace(stop=Event()))
        assert accepted and forced
        assert 'counter' not in record.call_args.kwargs['exact_inputs']['header']


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


def test_distinct_donor_references_preserve_two_relocations_to_one_loader_binding(tmp_path):
    from decompnet import consoles
    from decompnet.app.donor_relocations import separate_overlay_references
    from decompnet.app.source_symbol_recovery import rename
    from decompnet.elfobj import extract_function

    if toolchains.installed('ido-7.1') is None:
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    header = 'extern char first[]; extern char second[]; extern void step(int);'
    source = '''void entry(void) {
        int a = (int)first;
        int b = (int)second;
        *(int *)(a + 0x214) = 1;
        step(0);
        *(char *)(b + 0x378) = 0;
    }'''
    donor = compile_source(cfg, header, source)
    assert donor.ok, donor.log
    body = extract_function(donor.obj, 'entry')
    indices = {'first': 1, 'second': 1, 'step': 2}
    info = {'section': 64, 'start': 0, 'size': len(body.code),
            'relocs': [[r.offset, r.type, indices[r.symbol]] for r in body.relocs]}
    aliases = {name: uso_match.alias(64, index) for name, index in indices.items()}
    collapsed = compile_source(cfg, rename(header, aliases), rename(source, aliases))
    assert collapsed.ok, collapsed.log
    assert not uso_match.link(collapsed.obj, 'entry', body.code, info).exact

    distinct = separate_overlay_references(consoles.of({'platform': 'n64'}), aliases)
    assert distinct['first'] != distinct['second']
    verified = compile_source(cfg, rename(header, distinct), rename(source, distinct))
    assert verified.ok, verified.log
    assert uso_match.link(verified.obj, 'entry', body.code, info).exact
    wrong = distinct | {'second': uso_match.separate_reference(uso_match.alias(64, 3), 2)}
    compiled = compile_source(cfg, rename(header, wrong), rename(source, wrong))
    assert compiled.ok, compiled.log
    result = uso_match.link(compiled.obj, 'entry', body.code, info)
    assert result.instructions_exact and not result.exact


def test_fixed_offset_recovery_declines_a_partially_supported_rebase(tmp_path):
    from decompnet.app.overlay_donor_aliases import offset_retry
    from decompnet.elfobj import extract_function

    if not toolchains.installed('ido-7.1'):
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    header = 'extern volatile int mixed[128]; extern volatile int separate[128]; extern void consume(int);'
    source = 'int entry(void) { consume(mixed[2]); consume(separate[1]); return mixed[3]; }'
    baseline = compile_source(cfg, header, source)
    assert baseline.ok, baseline.log
    desired = compile_source(cfg, header, source.replace('mixed[2]', 'mixed[9]').replace(
        'mixed[3]', 'mixed[11]').replace('separate[1]', 'separate[2]'))
    assert desired.ok, desired.log
    out = extract_function(desired.obj, 'entry')
    indices = {'mixed': 1, 'separate': 2, 'consume': 3}
    info = {'section': 64, 'relocs': [[r.offset, r.type, indices[r.symbol]] for r in out.relocs]}
    expected = uso_match.symbolic_target(uso_match.Target('entry', 0, out.code, [], uso=info))
    original = uso_match.link(baseline.obj, 'entry', out.code, info).candidate
    assert offset_retry(source, header, 'entry', original, expected) is None


@pytest.mark.parametrize('definition', [
    '',  # An implicit C89 call still emits an ordinary external relocation.
    'placeholder() { }',
    'int placeholder(int value) { return value + 3; }',
    'int placeholder(value) short value; { return value + 3; }',
])
def test_overlay_probes_calls_to_a_defined_donor_helper(tmp_path, definition):
    from threading import Event

    from decompnet.app.overlay_donor_aliases import retry
    from decompnet.elfobj import extract_function

    if not toolchains.installed('ido-7.1'):
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    header = definition + '\n'
    source = 'int entry(int value) { placeholder(value); return placeholder(value + 1); }'
    original = compile_source(cfg, header, source)
    assert original.ok, original.log
    body = extract_function(original.obj, 'entry')
    assert len(body.relocs) == 2
    info = {'section': 64, 'relocs': [[r.offset, r.type, i] for i, r in enumerate(body.relocs, 1)]}
    expected = uso_match.symbolic_target(uso_match.Target('entry', 0, body.code, [], uso=info))
    baseline = uso_match.link(original.obj, 'entry', body.code, info)
    assert not baseline.exact

    def measure(text, context):
        built = compile_source(cfg, context, text)
        assert built.ok, built.log
        return uso_match.link(built.obj, 'entry', body.code, info).candidate

    rewritten, context = retry(source, header, 'entry', baseline.candidate, expected, measure, Event())
    rebuilt = compile_source(cfg, context, rewritten)
    assert rebuilt.ok, rebuilt.log
    assert uso_match.link(rebuilt.obj, 'entry', body.code, info).exact
    wrong = compile_source(cfg, context, rewritten.replace(uso_match.alias(64, 2), uso_match.alias(64, 1)))
    assert wrong.ok, wrong.log
    assert not uso_match.link(wrong.obj, 'entry', body.code, info).exact


@pytest.mark.parametrize(('ctype', 'suffix', 'fmt', 'opcode'), [
    ('float', 'f', '>f', 0x31),
    ('double', '', '>d', 0x35),
])
def test_anonymous_object_pool_requires_cartridge_bytes_and_original_loader_sites(tmp_path, ctype, suffix, fmt, opcode):
    import json

    from decompnet.elfobj import extract_function

    if not toolchains.installed('ido-7.1'):
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    obj = compile_source(cfg, '', f'{ctype} entry(void) {{ return 0.4{suffix}; }}')
    assert obj.ok, obj.log
    fn = extract_function(obj.obj, 'entry')
    assert [r.type for r in fn.relocs] == [5, 6]
    code = bytearray(fn.code)
    load = fn.relocs[1].offset
    assert code[load] >> 2 == opcode
    code[load+2:load+4] = (32).to_bytes(2, 'big')

    def record(kind, body):
        return struct.pack('>III', kind, len(body), 0x10000000) + body

    def cartridge(value, rodata_relocs=b''):
        symbols = struct.pack('>6I', 0x520000, 4, 0, 0x740000, 0, 0)
        relocs = b''.join(struct.pack('>III', 0x13 if r.type == 5 else 0x12, r.offset + 4, 0) for r in fn.relocs)
        return (bytes(64) + struct.pack('>III', 0x12345678, 1, 1) + b'pool.uso\0'
                + struct.pack('>IIII', 0x12345678, 0, 0, 0)
                + record(1, symbols) + record(2, relocs) + record(4, rodata_relocs)
                + record(5, bytes(4) + bytes(code)) + record(7, bytes(32) + struct.pack(fmt, value))
                + record(11, b''))

    def placement(image):
        region, = uso.read(image)
        relocs = uso.function_relocations(region, 4, len(code))
        return {'section': region.offset, 'start': 4, 'size': len(code), 'relocs': relocs,
                'pool_loads': uso.function_pool_loads(region, 4, len(code), relocs)}

    image = cartridge(0.4)
    info = placement(image)
    assert info['pool_loads'] == [[load, 1, struct.pack(fmt, 0.4).hex()]]
    older = {key: value for key, value in info.items() if key != 'pool_loads'}
    row = {'id': 1, 'rom_offset': info['section'] + 4, 'target': bytes(code),
           'meta': json.dumps({'uso': older | {'native': True}})}
    update, = uso.metadata_updates(image, {}, [row])
    assert json.loads(update[1])['uso'] == info | {'native': True}
    assert not uso_match.link(obj.obj, 'entry', bytes(code), info | {'pool_loads': []}).exact
    assert uso_match.link(obj.obj, 'entry', bytes(code), info).exact
    assert uso.rebuild(image, [('entry', info, obj.obj)]) == image
    wrong = cartridge(0.4000000000000001 if ctype == 'double' else 0.7)
    assert not uso_match.link(obj.obj, 'entry', bytes(code), placement(wrong)).exact
    with pytest.raises(ValueError, match='constant pool metadata'):
        uso.rebuild(wrong, [('entry', info, obj.obj)])
    relocated = cartridge(0.4, struct.pack('>III', 0x14, 32 + struct.calcsize(fmt) - 4, 0))
    assert placement(relocated)['pool_loads'] == []


def test_refresh_adds_wider_pool_read_to_existing_metadata(monkeypatch):
    import json

    code = struct.pack('>III', 0x3C020000, 0xC4400000, 0xD4420008)
    region = uso.Text('pool.uso', 128, code, False, relocations=((0, 5, 1), (4, 6, 1), (8, 6, 1)),
                      rodata=bytes(range(16)), rodata_bases=(1,), rodata_relocations=())
    monkeypatch.setattr(uso, 'read', lambda image: [region])
    old_load = [4, 1, '00010203']
    info = {'section': 128, 'start': 0, 'size': len(code), 'native': True,
            'relocs': [[0, 5, 1], [4, 6, 1], [8, 6, 1]], 'pool_loads': [old_load]}
    row = {'id': 1, 'rom_offset': 128, 'target': code, 'meta': json.dumps({'uso': info})}
    _, refreshed = next(uso.metadata_updates(b'', {}, [row]))
    assert json.loads(refreshed)['uso']['pool_loads'] == [old_load, [8, 1, '08090a0b0c0d0e0f']]
    stale = info | {'pool_loads': [[4, 1, '00010204']]}
    assert list(uso.metadata_updates(b'', {}, [row | {'meta': json.dumps({'uso': stale})}])) == []


@pytest.mark.parametrize('definition', [
    'placeholder() { }',
    'int placeholder(int value) { return value + 3; }',
    'int placeholder(value) short value; { return value + 3; }',
])
def test_overlay_named_binding_externalizes_only_referenced_direct_calls(tmp_path, definition):
    from decompnet.app.overlay_donor_aliases import named_bindings
    from decompnet.app.source_symbol_recovery import relocation_aliases
    from decompnet.elfobj import extract_function

    if not toolchains.installed('ido-7.1'):
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    header = definition + '\nint real(void) { return 9; }\n'
    source = 'int entry(int value) { return placeholder(value); }'
    built = compile_source(cfg, header, source)
    assert built.ok, built.log
    body = extract_function(built.obj, 'entry')
    reloc, = body.relocs
    info = {'section': 64, 'relocs': [[reloc.offset, reloc.type, 1]]}
    target = uso_match.symbolic_target(uso_match.Target('entry', 0, body.code, [], uso=info))
    baseline = uso_match.link(built.obj, 'entry', body.code, info)
    aliases = relocation_aliases(baseline.candidate, target)
    assert aliases == {'placeholder': uso_match.alias(64, 1)}
    renamed = compile_source(cfg, header.replace('placeholder', aliases['placeholder']),
                             source.replace('placeholder', aliases['placeholder']))
    assert renamed.ok, renamed.log
    assert not uso_match.link(renamed.obj, 'entry', body.code, info).exact

    rewritten, context = named_bindings(source, header, baseline.candidate, aliases)
    assert 'int real(void) { return 9; }' in context
    rebuilt = compile_source(cfg, context, rewritten)
    assert rebuilt.ok, rebuilt.log
    assert uso_match.link(rebuilt.obj, 'entry', body.code, info).exact

    _, context = named_bindings(source, 'static int placeholder(void) { return 9; }', baseline.candidate, aliases)
    assert '{ return 9; }' in context


def test_overlay_probes_preserve_conflicting_types_for_one_loader_symbol(tmp_path):
    from threading import Event

    from decompnet import consoles
    from decompnet.app.donor_relocations import separate_overlay_references
    from decompnet.app.overlay_donor_aliases import retry
    from decompnet.elfobj import extract_function

    if not toolchains.installed('ido-7.1'):
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    header = 'extern int first[];\nextern unsigned char second;\nextern void placeholder(int);'
    source = 'void entry(void) { placeholder(first[0]); placeholder(second); }'
    built = compile_source(cfg, header, source)
    assert built.ok, built.log
    body = extract_function(built.obj, 'entry')
    calls = iter((2, 3))
    info = {'section': 64, 'relocs': [[r.offset, r.type, next(calls) if r.type == 4 else 1] for r in body.relocs]}
    target = uso_match.symbolic_target(uso_match.Target('entry', 0, body.code, [], uso=info))
    original = uso_match.link(built.obj, 'entry', body.code, info).candidate

    def measure(text, context):
        trial = compile_source(cfg, context, text)
        assert trial.ok, trial.log
        return uso_match.link(trial.obj, 'entry', body.code, info).candidate

    text, context = retry(source, header, 'entry', original, target, measure, Event())
    assert not compile_source(cfg, context, text).ok

    console = consoles.of({'platform': 'n64'})
    text, context = retry(source, header, 'entry', original, target, measure, Event(),
                          separate=lambda bindings: separate_overlay_references(console, bindings))
    rebuilt = compile_source(cfg, context, text)
    assert rebuilt.ok, rebuilt.log
    assert uso_match.link(rebuilt.obj, 'entry', body.code, info).exact
    assert not uso_match.link(rebuilt.obj, 'entry', body.code,
                              info | {'relocs': [[at, kind, 4 if index == 1 else index]
                                                for at, kind, index in info['relocs']]}).exact


def test_overlay_probes_cover_a_larger_aligned_caller(tmp_path):
    from threading import Event

    from decompnet.app.overlay_donor_aliases import retry
    from decompnet.elfobj import extract_function

    if not toolchains.installed('ido-7.1'):
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    header = 'extern void placeholder(int);'
    calls = '\n'.join(f'placeholder({i});' for i in range(24))
    source = f'void entry(void) {{\n{calls}\n}}'
    built = compile_source(cfg, header, source)
    assert built.ok, built.log
    body = extract_function(built.obj, 'entry')
    assert len(body.relocs) == 24
    info = {'section': 64, 'relocs': [[r.offset, r.type, i] for i, r in enumerate(body.relocs, 1)]}
    target = uso_match.symbolic_target(uso_match.Target('entry', 0, body.code, [], uso=info))
    original = uso_match.link(built.obj, 'entry', body.code, info).candidate
    stop = Event()
    probes = 0

    def measure(text, context):
        nonlocal probes
        probes += 1
        trial = compile_source(cfg, context, text)
        assert trial.ok, trial.log
        return uso_match.link(trial.obj, 'entry', body.code, info).candidate

    stop.set()
    assert retry(source, header, 'entry', original, target, measure, stop) is None
    assert probes == 0
    stop.clear()
    text, context = retry(source, header, 'entry', original, target, measure, stop)
    assert probes == 24
    rebuilt = compile_source(cfg, context, text)
    assert rebuilt.ok, rebuilt.log
    assert uso_match.link(rebuilt.obj, 'entry', body.code, info).exact


def test_overlay_relocates_bounded_compiler_jump_table(tmp_path):
    import json

    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection

    from decompnet.candidate_work import elf as candidate_elf
    from decompnet.elfobj import _function_symbols, extract_function

    if not toolchains.installed('ido-7.1'):
        pytest.skip('needs IDO 7.1')
    cfg = compiler_config(tmp_path, {'compiler': {
        'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-mips2'],
    }})
    body = """switch (index) {
    case 0: *out = 5; break;
    case 1: *out += 3; break;
    case 2: *out &= 7; break;
    case 3: *out = *out * 2; break;
    case 4: *out -= 19; break;
    case 5: *out |= 128; break;
    default: break;
    }"""
    built = compile_source(cfg, '', 'void entry(int index, volatile int *out) { ' + body + ' }')
    assert built.ok, built.log
    compiler = candidate_elf(built.obj, ELFFile)
    _, entry, _ = _function_symbols(compiler)['entry']
    fn = extract_function(built.obj, 'entry')
    high, low = fn.relocs
    assert (high.type, low.type) == (5, 6)
    rodata = compiler.get_section_by_name('.rodata')
    pointers = next(section for section in compiler.iter_sections()
                    if isinstance(section, RelocationSection) and section.name == '.rel.rodata')
    locations = [r['r_offset'] for r in pointers.iter_relocations()]
    offsets = [int.from_bytes(rodata.data()[at:at + 4], 'big') - entry for at in locations]
    assert len(offsets) == 6
    start, table_offset = 144, 64
    target = fn.code[:low.offset + 2] + table_offset.to_bytes(2, 'big') + fn.code[low.offset + 4:]

    def record(kind, content):
        return struct.pack('>III', kind, len(content), 0x10000000) + content

    def cartridge(cases, missing=False):
        symbols = struct.pack('>9I', 0x520000, start, 0, 0x740000, 0, 0, 0x540000, 0, 0)
        shifts = struct.pack('>6I', 0x13, start + high.offset, 0, 0x12, start + low.offset, 0)
        links = b''.join(struct.pack('>III', 0x24, table_offset + i * 4, 0)
                         for i in range(len(cases) - missing))
        data = bytes(table_offset) + b''.join(struct.pack('>I', start + offset) for offset in cases)
        return (bytes(64) + struct.pack('>III', 0x12345678, 1, 1) + b'table.uso\0'
                + struct.pack('>IIII', 0x12345678, 0, 0, 0)
                + record(1, symbols) + record(2, shifts) + record(4, links)
                + record(5, bytes(start) + target) + record(7, data) + record(11, b''))

    image = cartridge(offsets)
    region, = uso.read(image)
    relocs = uso.function_relocations(region, start, len(target))
    info = {'section': region.offset, 'start': start, 'size': len(target), 'relocs': relocs, 'native': True}
    row = {'id': 1, 'rom_offset': region.offset + start, 'target': target, 'meta': json.dumps({'uso': info})}
    _, updated = next(uso.metadata_updates(image, {}, [row]))
    refreshed = json.loads(updated)['uso']
    assert refreshed['jump_tables'] == [[low.offset, 1, offsets]]
    assert not uso_match.link(built.obj, 'entry', target, info).exact
    assert uso_match.link(built.obj, 'entry', target, refreshed).exact
    assert uso.rebuild(image, [('entry', refreshed, built.obj)]) == image

    changed = [offsets[1], offsets[0], *offsets[2:]]
    wrong_image = cartridge(changed)
    wrong_region, = uso.read(wrong_image)
    assert not uso_match.link(built.obj, 'entry', target, refreshed | {
        'jump_tables': uso.function_jump_tables(wrong_region, start, len(target), relocs),
    }).exact
    with pytest.raises(ValueError, match='jump table metadata'):
        uso.rebuild(wrong_image, [('entry', refreshed, built.obj)])
    incomplete, = uso.read(cartridge(offsets, missing=True))
    assert not uso.function_jump_tables(incomplete, start, len(target), relocs)


def audit_worker():
    """Require the shared verifier to negotiate and execute the native overlay request."""
    from decompnet.app.cli import main
    from decompnet.rom import verifier_link

    def require_native(*args):
        assert verifier_link.overlay_linker.get() is not None, "the verifier did not negotiate the USO linker"
        return _python_link(*args)

    verifier_link.link_overlay = lambda obj, name, code, info, exchange: audit_overlay(exchange, obj, name, code, info)
    uso_match.link = require_native
    return main()


@pytest.mark.parametrize("toolchain", ["gcc-2.7.2-kmc", "ido-7.1"])
def test_uso_desktop_and_shared_queue_use_rust(tmp_path, monkeypatch, toolchain):
    import hashlib
    import json

    from decompnet.app import attempt_workers, mcp_gateway
    from decompnet.elfobj import extract_function
    from tests.test_hosted_pack import game
    from tests.test_rust_mips_link import queue_parity

    if not mcp_gateway.built() or not toolchains.installed(toolchain):
        pytest.skip("needs the Rust gateway and the pinned compiler")

    net = game(tmp_path)
    project = net.project_row("game", sync=False)
    header = "typedef int s32;"
    compiler = {"toolchain": toolchain, "flags": list(toolchains.get(toolchain).flags)}
    exact = "extern int uso_40_sym_1(int); int f1(int x) { return uso_40_sym_1(x + 3); }"
    wrong = exact.replace("uso_40_sym_1", "uso_40_sym_2")
    built = compile_source(compiler_config(tmp_path, {"compiler": compiler}), header, exact)
    assert built.ok, built.log
    body = extract_function(built.obj, "f1")
    assert body is not None and [r.type for r in body.relocs] == [4]
    start, bank = 0x100, 0x1000
    info = {"section": 0x40, "start": start, "size": len(body.code),
            "relocs": [[r.offset, r.type, 1] for r in body.relocs]}
    cfg = json.loads(project["config"])
    cfg.pop("progress_folder", None)
    cfg.pop("collab", None)
    cfg.update(compiler=compiler, compiler_ranges=[], donor_overlays={"64": bank})
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), project["id"]))
        db.execute("UPDATE functions SET target=?, size=?, n_insns=?, vram=?, rom_offset=?, meta=? WHERE name='f1'",
                   (body.code, len(body.code), len(body.code) // 4, start, bank + start, json.dumps({"uso": info})))

    request = {"slug": "game", "function": {"name": "f1", "vram": start, "target": body.code.hex()},
               "compiler": compiler, "header_sha256": hashlib.sha256(header.encode()).hexdigest()}
    from decompnet.rom.verifier_link import overlay_linker

    fn = net._function("f1", "game")
    proj = net.project_row("game", sync=False)
    bad = compile_source(compiler_config(tmp_path, {"compiler": compiler}), header, wrong)
    assert bad.ok, bad.log
    batch = net._judge_random_batch(fn, proj, [built.obj, bad.obj, b""])
    assert batch is not None
    _, judgments = batch
    assert judgments[-1] is None  # A declined input leaves its neighbors and their order intact.
    token = overlay_linker.set(None)
    try:
        from decompnet import scoring

        for obj, (candidate, diff, clean) in zip((built.obj, bad.obj), judgments[:2], strict=True):
            oracle = _python_link(obj, fn["name"], body.code, info)
            score = scoring._diff(net._target_obj(fn, proj), oracle.candidate, details=False)
            assert candidate == oracle.candidate and clean == (not oracle.problems)
            assert (diff.score, diff.bytes_match) == (0 if oracle.exact else max(1, score.score), oracle.exact)
    finally:
        overlay_linker.reset(token)

    token = overlay_linker.set(None)
    try:
        with monkeypatch.context() as python:
            python.setenv("DECOMP_MCP_GATEWAY", "0")
            scores = queue_parity(tmp_path, net, request, [exact, wrong], worker_module=__name__)
    finally:
        overlay_linker.reset(token)
    assert scores[0] == 0 and scores[1] > 0

    trace = tmp_path / "rust-uso-trace"
    worker = tmp_path / "uso-worker.py"
    original = attempt_workers.WORKER_SOURCE.read_text()
    worker.write_text(original.replace("    serve()", f'''
    from {__name__} import audit_overlay
    def checked_link(self, *args):
        result = audit_overlay(self._exchange, *args)
        with Path({str(trace)!r}).open("a") as stream:
            stream.write(str(result.exact) + "\\n")
        return result
    RustJudge.link_overlay = checked_link
    serve()'''))
    monkeypatch.setattr(attempt_workers, "WORKER_SOURCE", worker)
    net.attempt_workers = pool = attempt_workers.Pool(tmp_path, size=1)
    agent, _ = net.local_agent("overlay-tester")
    try:
        net.claim_row(agent, "game", "f1")
        attempt = net.attempt(agent, "f1", wrong, "game")
        assert not attempt["matched"] and attempt["score"] == scores[1]
        interactive = net.function_diff("game", "f1")
        assert interactive["score"] == attempt["score"] and interactive["checks"]["instructions"]["exact"]
        assert interactive["problems"] and "uso_40_sym_1" in interactive["problems"][0]
        attempt = net.attempt(agent, "f1", exact, "game")
        assert attempt["matched"] and attempt["score"] == 0
        assert trace.read_text().splitlines() == ["False", "False", "True"]
    finally:
        pool.close()
