"""Target objects must preserve instructions and the destinations the original code uses."""
import importlib.util
import io
import json
import shutil
import subprocess
import sys

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.consoles.ps4 import scratch


def delinker():
    spec = importlib.util.spec_from_file_location('ps4_delink_test', scratch.TEMPLATE / 'tools/delink.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def image(code, base=0x1000):
    return scratch.ELF(code, base, 2, [scratch.Segment(base, 0, len(code), len(code), 1)])


def object_for(code, relocs, size=None):
    f = {'name': 'demo', 'addr': 0, 'size': size or len(code), 'relocs': relocs}
    return delinker().elf64('demo', [f], code, 0)


def refs(blob):
    elf = ELFFile(io.BytesIO(blob))
    syms = elf.get_section_by_name('.symtab')
    return [(r['r_offset'], r['r_info_type'], syms.get_symbol(r['r_info_sym']).name, r['r_addend'])
            for r in elf.get_section_by_name('.rela.text').iter_relocations()]


def test_symbol_index_accounts_for_null_and_reuses_defined_symbols():
    code = bytes.fromhex('e800000000e900000000')
    obj = object_for(code, [(1, 2, 'external', -4), (6, 2, 'demo', -4)])
    assert refs(obj) == [(1, 2, 'external', -4), (6, 2, 'demo', -4)]
    names = [s.name for s in ELFFile(io.BytesIO(obj)).get_section_by_name('.symtab').iter_symbols()]
    assert names.count('demo') == 1


def test_short_branch_masks_only_its_one_byte_operand():
    code = bytes.fromhex('4885ff7405c390909090c3')
    fn = scratch.analyse(image(code), [{'entry': 0x1000, 'end': 0x1005}])[0]
    assert fn.relocs == [(4, 15, 'D_0000100A', -1)]
    obj = object_for(code[:5], fn.relocs)
    assert ELFFile(io.BytesIO(obj)).get_section_by_name('.text').data() == bytes.fromhex('4885ff7400')


def test_internal_short_branch_is_not_relocated_when_true_end_is_known():
    code = bytes.fromhex('4885ff7405c390909090c3')
    fn = scratch.analyse(image(code), [{'entry': 0x1000, 'end': 0x100b}])[0]
    assert fn.relocs == []


def test_rip_displacement_addend_accounts_for_trailing_immediate():
    code = bytes.fromhex('c7050100000007000000c3') + b'\0' * 16
    fn = scratch.analyse(image(code), [{'entry': 0x1000, 'end': 0x100b}])[0]
    assert fn.relocs == [(2, 2, 'D_0000100B', -8)]


def test_discontinuous_body_keeps_span_and_reads_later_block():
    code = bytes.fromhex('c3909090e801000000c3c3')
    fn = scratch.analyse(image(code), [{'entry': 0x1000, 'end': 0x100a,
                                       'ranges': [[0x1000, 0x1001], [0x1004, 0x100a]]}])[0]
    assert fn.size == 10
    assert fn.relocs == [(5, 2, 'D_0000100A', -4)]


def test_relocations_are_not_pruned_by_decoding_padding():
    fn = scratch.Function(0x1000, 5, 'demo', [(1, 2, 'external', -4)])
    scratch.prune_unit_relocs(image(bytes.fromhex('e800000000')), [fn])
    assert fn.relocs == [(1, 2, 'external', -4)]


@pytest.mark.parametrize('reloc', [(-1, 2, 'external', -4), (3, 2, 'external', -4), (1, 99, 'external', 0)])
def test_invalid_relocation_bounds_fail_instead_of_corrupting_neighbors(reloc):
    with pytest.raises(ValueError, match='invalid relocation'):
        object_for(bytes.fromhex('e800000000c3'), [reloc], size=5)


def test_legacy_address_count_is_not_used_as_contiguous_size(tmp_path):
    (tmp_path / 'functions.jsonl').write_text(json.dumps({'a': '0', 's': 5}) + '\n')
    assert scratch.map_functions(tmp_path / 'game.elf', image(b'\xc3'*20, 0).text) is None


def test_explicit_end_wins_over_body_byte_count(tmp_path):
    (tmp_path / 'functions.jsonl').write_text(json.dumps({'a': '0', 's': 5, 'end': 'b',
                                                        'ranges': [[0, 1], [4, 11]]}) + '\n')
    row = scratch.map_functions(tmp_path / 'game.elf', image(b'\xc3'*20, 0).text)[0]
    assert row['end'] == 11
    assert row['ranges'] == [[0, 1], [4, 11]]


def test_generated_project_requires_relocation_identity(tmp_path):
    root = tmp_path / 'project'
    shutil.copytree(scratch.TEMPLATE, root)
    (root / 'config/eboot').mkdir(parents=True)
    (root / 'config/eboot/functions.json').write_text(json.dumps({'units': {}}))
    subprocess.run([sys.executable, str(root / 'tools/configure.py'), 'eboot', '--clang', 'unused-clang'],
                   check=True, capture_output=True)
    cfg = json.loads((root / 'objdiff.json').read_text())
    assert cfg['options']['functionRelocDiffs'] == 'all'


def test_objdiff_rejects_wrong_call_and_accepts_same_call_with_strict_options(tmp_path):
    exe = toolchains.binary('objdiff-cli')
    if not exe.exists():
        pytest.skip('pinned objdiff is not installed here')
    code = bytes.fromhex('e800000000c3')
    target, base = tmp_path / 'target.o', tmp_path / 'base.o'
    target.write_bytes(object_for(code, [(1, 2, 'correct', -4)]))
    for callee, matches in [('wrong', False), ('correct', True)]:
        base.write_bytes(object_for(code, [(1, 2, callee, -4)]))
        result = subprocess.run([str(exe), 'diff', '-1', str(target), '-2', str(base), '-o', '-',
                                 '-c', 'functionRelocDiffs=all', 'demo'], capture_output=True, text=True, check=True)
        symbols = json.loads(result.stdout)['left']['symbols']
        percent = next(s['match_percent'] for s in symbols if s['name'] == 'demo')
        assert (percent == 100) == matches


def test_shared_tail_in_another_function_does_not_swallow_intervening_functions():
    code = bytes.fromhex('eb02c390c3')
    rows = [{'entry': 0x1000, 'end': 0x1005, 'ranges': [[0x1000, 0x1002], [0x1004, 0x1005]]},
            {'entry': 0x1002, 'end': 0x1003, 'ranges': [[0x1002, 0x1003]]},
            {'entry': 0x1004, 'end': 0x1005, 'ranges': [[0x1004, 0x1005]]}]
    fn = scratch.analyse(image(code), rows)[0]
    assert fn.size == 2
    assert fn.relocs == [(1, 15, 'func_00001004', -1)]


def test_shared_tail_before_entry_is_external_not_a_negative_relocation():
    code = bytes.fromhex('c39090ebfb')
    rows = [{'entry': 0x1000, 'end': 0x1001, 'ranges': [[0x1000, 0x1001]]},
            {'entry': 0x1003, 'end': 0x1005, 'ranges': [[0x1000, 0x1001], [0x1003, 0x1005]]}]
    fn = scratch.analyse(image(code), rows)[1]
    assert fn.size == 2
    assert fn.relocs == [(1, 15, 'func_00001000', -1)]


def test_typed_linkage_declaration_is_allowed_but_assembly_is_not():
    from decompnet.app.forkengine import _CHEAT, _strip_comments

    declaration = 'extern __typeof__(old) recovered __asm__("correct") __attribute__((visibility("hidden")));'
    assert not _CHEAT.search(_strip_comments(declaration))
    for text in ['__asm__("correct");', 'asm volatile("nop");',
                 declaration.replace('"correct"', '"correct\\n nop"'),
                 declaration + ' __asm__("nop");']:
        assert _CHEAT.search(_strip_comments(text))


def test_rip_reference_to_bss_is_relocated_without_file_backing():
    code = bytes.fromhex('488d05f90f0000c3')
    elf = image(code)
    elf.segments.append(scratch.Segment(0x2000, len(code), 0, 0x100, 6))
    fn = scratch.analyse(elf, [{'entry': 0x1000, 'end': 0x1008}])[0]
    assert fn.relocs == [(3, 2, 'D_00002000', -4)]
    assert elf.offset(0x2000) is None
    assert elf.read(0x2000, 8) == b''
    assert not elf.contains(0x2000)


def test_rip_reference_past_mapped_memory_stays_literal():
    code = bytes.fromhex('488d05f9100000c3')
    elf = image(code)
    elf.segments.append(scratch.Segment(0x2000, len(code), 0, 0x100, 6))
    fn = scratch.analyse(elf, [{'entry': 0x1000, 'end': 0x1008}])[0]
    assert fn.relocs == []


def test_bss_reference_with_trailing_immediate_keeps_pc_addend():
    code = bytes.fromhex('c705f60f000007000000c3')
    elf = image(code)
    elf.segments.append(scratch.Segment(0x2000, len(code), 0, 0x100, 6))
    fn = scratch.analyse(elf, [{'entry': 0x1000, 'end': 0x100b}])[0]
    assert fn.relocs == [(2, 2, 'D_00002000', -8)]


def make_elf(code=b'\xc3', *, phentsize=56, filesz=None, memsz=None, kind=1):
    """A synthetic header-only Orbis ELF; no bytes come from a game."""
    import struct

    header = bytearray(128)
    header[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HHIQQQIHHHHHH', header, 16, 0xFE10, 62, 1, 0x1000, 64, 0, 0, 64,
                     phentsize, 1, 0, 0, 0)
    struct.pack_into('<IIQQQQQQ', header, 64, kind, 5, 128, 0x1000, 0, len(code) if filesz is None else filesz,
                     len(code) if memsz is None else memsz, 16)
    return bytes(header)+code


@pytest.mark.parametrize('fields', [{'phentsize': 0}, {'filesz': 100, 'memsz': 100}, {'memsz': 0}])
def test_malformed_elf_is_rejected_with_an_import_error(fields):
    from decompnet.forks.project import ForkError

    with pytest.raises(ForkError):
        scratch.read_elf(make_elf(**fields))


def test_relro_bytes_are_loader_mapped_and_reads_stop_at_the_segment():
    elf = scratch.read_elf(make_elf(b'\x12\x34', kind=scratch.PT_SCE_RELRO))
    assert elf.read(0x1001, 100) == b'\x34'
    assert elf.read(0x1002, 1) == b''


def test_only_complete_nop_instructions_are_padding():
    assert scratch.is_padding(bytes.fromhex('660f1f44000090cc'))
    assert not scratch.is_padding(bytes.fromhex('0f1f0000'))  # NOP then an incomplete ADD
    assert not scratch.is_padding(bytes.fromhex('00'))  # formerly accepted by a byte whitelist
    assert not scratch.is_padding(bytes.fromhex('90c3'))


def test_thunks_are_real_functions_and_executable_segments_do_not_share_units():
    elf = scratch.ELF(bytes.fromhex('c3c3'), 0x1000, 0xFE10,
                      [scratch.Segment(0x1000, 0, 1, 1, 5), scratch.Segment(0x1010, 1, 1, 1, 5)])
    funcs = scratch.analyse(elf, [{'entry': 0x1000, 'end': 0x1001, 'thunk': True},
                                {'entry': 0x1010, 'end': 0x1011}])
    assert len(funcs) == 2
    assert len(scratch.units(funcs, elf)) == 2


def test_renaming_a_function_also_updates_relocation_references(tmp_path):
    from types import SimpleNamespace

    path = tmp_path/'config/demo/functions.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'units': {'u': [{'name': 'old', 'relocs': []},
                                              {'name': 'caller', 'relocs': [[1, 2, 'old', -4]]}]}}))
    before = path.read_text()
    changed = scratch.rename(SimpleNamespace(root=tmp_path), 'demo', 'old', 'new')
    assert changed[path] == before
    units = json.loads(path.read_text())['units']['u']
    assert units[0]['name'] == 'new' and units[1]['relocs'][0][2] == 'new'


def test_console_detection_and_draft_types_are_integrated(tmp_path):
    from decompnet import consoles

    ps4 = consoles.get('ps4')
    assert consoles.get('orbis') is ps4
    assert ps4.sniff(make_elf(), tmp_path/'eboot.elf')
    assert not ps4.sniff(b'\x4f\x15\x3d\x1d'+b'\0'*128, tmp_path/'eboot.bin')
    assert ps4.adapt_draft('undefined8 a; undefined4 b;') == 'u64 a; u32 b;'
    assert consoles.get('pc').adapt_draft('original') == 'original'


def test_compiler_comes_only_from_the_pinned_registry(monkeypatch, tmp_path):
    monkeypatch.setenv('DECOMP_PS4_CLANG', str(tmp_path/'unverified-clang'))
    monkeypatch.setattr(toolchains, 'installed', lambda *a, **kw: None)
    assert scratch.clang_path() is None
    spec = toolchains.get('clang-5.0.2')
    assert len(spec.parts[0]['sha256']) == 64
    assert spec.cc[1:3] == ['-target', 'x86_64-scei-ps4']


def binder():
    spec = importlib.util.spec_from_file_location('ps4_bind_test', scratch.TEMPLATE / 'tools/bind.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def got_object_for(code, relocations):
    """Model a compiler's GOT relocations without widening the target delinker."""
    import struct

    blob = bytearray(object_for(code, [(offset, 2, name, addend) for offset, _, name, addend in relocations]))
    elf = ELFFile(io.BytesIO(blob))
    section = elf.get_section_by_name('.rela.text')
    for index, (_, kind, _, _) in enumerate(relocations):
        at = section['sh_offset'] + index*24 + 8
        info, = struct.unpack_from('<Q', blob, at)
        struct.pack_into('<Q', blob, at, (info & ~0xffffffff) | kind)
    return bytes(blob)


def test_guard_binding_is_explicit_and_keeps_a_named_target_slot(tmp_path):
    code = bytes.fromhex('488b0500000000c3')
    source = tmp_path/'source.o'
    target = tmp_path/'text_00001000.o'
    original = got_object_for(code, [(3, 9, '__stack_chk_guard', -4)])
    source.write_bytes(original)
    target.write_bytes(object_for(code, [(3, 2, 'D_00002000', -4)]))
    assert binder().bind(source, target, None) == 0
    assert source.read_bytes() == original
    assert binder().bind(source, target, 0x2000) == 1
    assert refs(source.read_bytes()) == [(3, 2, 'D_00002000', -4)]
    assert ELFFile(io.BytesIO(source.read_bytes())).get_section_by_name('.text').data() == code


def test_got_relaxation_uses_internal_target_names_and_preserves_imports(tmp_path):
    source, target = tmp_path/'source.o', tmp_path/'text_00001000.o'
    code = bytes.fromhex('488b0500000000488b0500000000c3')
    source.write_bytes(got_object_for(code, [(3, 9, 'D_00002000', -4), (10, 9, 'imported', -4)]))
    target.write_bytes(object_for(code, [(3, 2, 'D_00002000', -4)]))
    assert binder().relax_got(source, target) == 1
    text = ELFFile(io.BytesIO(source.read_bytes())).get_section_by_name('.text').data()
    assert text[1] == 0x8d and text[8] == 0x8b
    assert refs(source.read_bytes()) == [(3, 2, 'D_00002000', -4), (10, 9, 'imported', -4)]


def test_stock_ps4_target_uses_a_global_guard_without_a_compiler_patch(tmp_path):
    compiler = scratch.clang_path()
    if compiler is None:
        pytest.skip('needs pinned public clang 5.0.2')
    from decompnet.consoles.ps4.project_template.tools.compiler_flags import CL_FLAGS

    source, obj = tmp_path/'guard.c', tmp_path/'guard.o'
    source.write_text('extern void consume(void *); void guarded(void) { char b[64]; consume(b); }')
    subprocess.run([str(compiler), *CL_FLAGS, '-c', str(source), '-o', str(obj)], check=True, timeout=60)
    with obj.open('rb') as file:
        elf = ELFFile(file)
        symbols = elf.get_section_by_name('.symtab')
        relocations = [symbols.get_symbol(r['r_info_sym']).name
                       for section in elf.iter_sections() if section['sh_type'] == 'SHT_RELA'
                       for r in section.iter_relocations()]
    assert '__stack_chk_guard' in relocations and '__stack_chk_fail' in relocations


def test_import_build_and_match_work_in_a_directory_with_spaces(tmp_path):
    compiler = scratch.clang_path()
    if compiler is None or toolchains.installed('objdiff-cli') is None:
        pytest.skip('needs pinned public clang 5.0.2 and objdiff')
    from decompnet.consoles.ps4.project_template.tools.compiler_flags import CL_FLAGS
    from decompnet.forks.project import ForkProject
    from decompnet.limits import capped

    source, original = tmp_path/'original.c', tmp_path/'original.o'
    code = 'int absolute(int v) { return v < 0 ? -v : v; }'
    source.write_text(code)
    subprocess.run(capped([str(compiler), *CL_FLAGS, '-c', str(source), '-o', str(original)]), check=True, timeout=60)
    with original.open('rb') as file:
        elf = ELFFile(file)
        section = elf.get_section_by_name('.text.absolute')
        if section is None:
            section = elf.get_section_by_name('.text')
        text = section.data()
    game = tmp_path/'demo.elf'
    game.write_bytes(make_elf(text))
    game.with_suffix('.functions.jsonl').write_text(json.dumps({'a': '0', 'end': f'{len(text):x}'})+'\n')
    root = tmp_path/'project with spaces'
    scratch.create(root, game, log=lambda *_: None)
    candidate = root/'src/auto/text_00001000.c'
    candidate.write_text(code.replace('absolute', 'func_00001000'))
    project = ForkProject(root)
    ok, detail = project.run(['ninja', 'prepare'], log=lambda *_: None)
    assert ok, detail
    exe = toolchains.binary('objdiff-cli')
    for body, matched in [(code, True), (code.replace('-v : v', '-v : v+1'), False)]:
        candidate.write_text(body.replace('absolute', 'func_00001000'))
        ok, detail = project.run(['ninja', 'prepare'], log=lambda *_: None)
        assert ok, detail
        result = subprocess.run([str(exe), 'diff', '-1', str(root/'build/demo/target/text_00001000.o'),
                                 '-2', str(root/'build/demo/src/auto/text_00001000.o'),
                                 '-c', 'functionRelocDiffs=all', '-o', '-'], capture_output=True, text=True,
                                check=True, timeout=60)
        symbol = next(s for s in json.loads(result.stdout)['left']['symbols'] if s['name'] == 'func_00001000')
        assert (symbol['match_percent'] == 100) is matched


def test_x86_64_twins_mask_a_short_operand_without_erasing_the_next_instruction():
    from decompnet.app.twins import object_prints

    first = object_prints(object_for(bytes.fromhex('eb00c3'), [(1, 15, 'first', -1)]))['demo']
    second = object_prints(object_for(bytes.fromhex('eb00c3'), [(1, 15, 'second', -1)]))['demo']
    changed = object_prints(object_for(bytes.fromhex('eb0090'), [(1, 15, 'second', -1)]))['demo']
    assert first.key == second.key
    assert first.key != changed.key


def test_ps4_permuting_uses_a_supported_weight_set():
    from decompnet.app.permute import weight_family

    assert weight_family({'platform': 'ps4'}, None, True) == 'base'
