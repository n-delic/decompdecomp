import struct
from contextlib import nullcontext
from dataclasses import replace
from inspect import unwrap
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from decompnet import consoles, toolchains
from decompnet.app import donor_build, donor_switch_tables, exact_match_inputs
from decompnet.candidate_work import elf as candidate_elf
from decompnet.compiler import compile_source
from decompnet.consoles.n64 import uso, uso_match
from decompnet.elfobj import _function_symbols, extract_function
from decompnet.project import compiler_config

DONOR = '''void entry(int x, volatile int *p) {
    switch (x) {
    case 100: *p=1; return;
    case 101: *p=2; return;
    case 102: *p=3; return;
    case 103: *p=4; return;
    case 108: return;
    }
    *p=9;
}'''
TARGET = '''void entry(int x, volatile int *p) {
    switch (x) {
    case 108: *p=1; return;
    case 100: *p=2; return;
    case 101: *p=3; return;
    case 103: *p=4; return;
    case 102: break;
    }
    *p=9;
}'''


def test_donor_switch_labels_use_verified_compiler_and_overlay_tables(tmp_path, monkeypatch):
    if toolchains.installed('ido-7.1') is None:
        pytest.skip('needs IDO 7.1')
    flags = ['-O2', '-G', '0', '-non_shared', '-mips2']
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-7.1', 'flags': flags}})
    donor, wanted = compile_source(cfg, '', DONOR), compile_source(cfg, '', TARGET)
    assert donor.ok and wanted.ok, (donor.log, wanted.log)
    body = extract_function(wanted.obj, 'entry')
    high, low = body.relocs
    assert (high.type, low.type) == (5, 6)
    elf = candidate_elf(wanted.obj, ELFFile)
    _, entry, _ = _function_symbols(elf)['entry']
    data = elf.get_section_by_name('.rodata')
    relocations = next(section for section in elf.iter_sections()
                       if isinstance(section, RelocationSection) and section.name == '.rel.rodata')
    positions = [reloc['r_offset'] for reloc in relocations.iter_relocations()]
    destinations = [int.from_bytes(data.data()[at:at + 4], 'big') - entry for at in positions]
    assert len(destinations) == 9
    start, table_offset = 4, 64
    code = bytearray(body.code)
    code[low.offset + 2:low.offset + 4] = table_offset.to_bytes(2, 'big')
    code = bytes(code)

    def record(kind, content):
        return struct.pack('>III', kind, len(content), 0x10000000) + content

    symbols = struct.pack('>9I', 0x520000, start, 0, 0x740000, 0, 0, 0x540000, 0, 0)
    text_relocs = struct.pack('>6I', 0x13, start + high.offset, 0, 0x12, start + low.offset, 0)
    pool_relocs = b''.join(struct.pack('>III', 0x24, table_offset + i * 4, 0)
                           for i in range(len(destinations)))
    pool = bytes(table_offset) + b''.join(struct.pack('>I', start + offset) for offset in destinations)
    cartridge = (bytes(64) + struct.pack('>III', 0x12345678, 1, 1) + b'table.uso\0'
                 + struct.pack('>IIII', 0x12345678, 0, 0, 0) + record(1, symbols) + record(2, text_relocs)
                 + record(4, pool_relocs) + record(5, bytes(start) + code) + record(7, pool) + record(11, b''))
    region, = uso.read(cartridge)
    relocs = uso.function_relocations(region, start, len(code))
    info = {'section': region.offset, 'start': start, 'size': len(code), 'relocs': relocs,
            'jump_tables': uso.function_jump_tables(region, start, len(code), relocs)}
    assert info['jump_tables'] == [[low.offset, 1, destinations]]
    placement = uso_match.Target('entry', start, code, [], uso=info)
    target = uso_match.symbolic_target(placement)
    baseline = uso_match.link(donor.obj, 'entry', code, info)
    assert baseline.candidate and not baseline.exact
    console = consoles.of({'platform': 'n64'})
    record_match = Mock()
    net = SimpleNamespace(
        _compile=lambda _project, header, source, _fn: compile_source(cfg, header, source),
        _evaluate=lambda _fn, _project, obj: (lambda result: (result.candidate, result.exact, result.problems))(
            uso_match.link(obj, 'entry', code, info)),
        _rom_target=lambda _fn: placement, _target_obj=lambda *_: target,
        console=lambda _: console, _record=record_match,
        progress=SimpleNamespace(judging=lambda *_args, **_kwargs: nullcontext()))
    fn = {'name': 'entry', 'size': len(code), 'id': 1}
    suggestions = donor_switch_tables.variants(net, {}, fn, DONOR, donor.obj, baseline.candidate)
    assert len(suggestions) == 1
    changed = compile_source(cfg, '', suggestions[0][1])
    assert changed.ok, changed.log
    assert uso_match.link(changed.obj, 'entry', code, info).exact
    assert uso.rebuild(cartridge, [('entry', info, changed.obj)]) == cartridge
    assert not donor_switch_tables.variants(net, {}, fn, DONOR, b'invalid object', baseline.candidate)
    different = bytes([baseline.candidate.code[0] ^ 1]) + baseline.candidate.code[1:]
    assert not donor_switch_tables.variants(net, {}, fn, DONOR, donor.obj, replace(baseline.candidate, code=different))
    without_table = uso_match.Target('entry', start, code, [], uso=info | {'jump_tables': []})
    net._rom_target = lambda _fn: without_table
    assert not donor_switch_tables.variants(net, {}, fn, DONOR, donor.obj, baseline.candidate)
    net._rom_target = lambda _fn: placement
    monkeypatch.setattr(exact_match_inputs, 'capture', lambda *_: {})
    monkeypatch.setattr(donor_build, 'acceptable', lambda *_: True)
    accepted, _ = unwrap(donor_build._try)(net, {'slug': 'test', 'arch': 'mips'}, {}, fn, DONOR, '',
                                          SimpleNamespace(id=1), SimpleNamespace(stop=Event()))
    assert accepted
    saved = record_match.call_args.kwargs['exact_inputs']
    replayed = compile_source(cfg, saved['header'], saved['source'])
    assert replayed.ok, replayed.log
    assert uso_match.link(replayed.obj, 'entry', code, info).exact
