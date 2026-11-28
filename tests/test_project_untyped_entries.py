"""Closed untyped MIPS entries remain visible to reports and the judge."""
import json
import subprocess

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.forks import objdiff, untyped_entries


def assemble(tmp_path, *, closed=True, child='func_80001008'):
    binary = toolchains.binary('binutils-mips') if toolchains.installed('binutils-mips') else None
    if binary is None:
        pytest.skip('needs pinned MIPS binutils')
    source = tmp_path/'target.s'
    source.write_text('.text\n.set noreorder\n.globl func_80001000\n.globl '+child+'\n'
                      'func_80001000:\n'+('jr $31\nnop\n' if closed else 'nop\nnop\n')+
                      child+':\njr $31\nnop\n.size func_80001000, .-func_80001000\n')
    target = tmp_path/'target.o'
    assembler = binary.parent/'mips-ps2-decompals-as'
    subprocess.run([str(assembler), '-EB', '-mips2', '-no-pad-sections', '-o', str(target), str(source)],
                   check=True, capture_output=True)
    return target


def symbols(path):
    with path.open('rb') as stream:
        elf = ELFFile(stream)
        return {s.name: (s['st_info']['type'], s['st_size']) for s in elf.get_section_by_name('.symtab').iter_symbols()}


def test_view_splits_closed_entries_without_changing_original(tmp_path):
    target = assemble(tmp_path)
    original = target.read_bytes()
    view = untyped_entries.view(target, tmp_path, 'view.o')
    assert view != target
    assert symbols(target)['func_80001000'][1] == 16
    assert symbols(view)['func_80001000'][1] == 8
    assert symbols(view)['func_80001008'] == ('STT_FUNC', 8)
    assert target.read_bytes() == original


def test_view_rejects_fallthrough_or_mismatched_address(tmp_path):
    target = assemble(tmp_path, closed=False)
    assert untyped_entries.view(target, tmp_path, 'view.o') == target
    target = assemble(tmp_path, child='func_8000100C')
    assert untyped_entries.view(target, tmp_path, 'view.o') == target


def test_view_rejects_a_changed_object_even_if_a_cached_plan_exists(tmp_path, monkeypatch):
    target = assemble(tmp_path)
    monkeypatch.setattr(untyped_entries, '_edits', lambda *_: (bytes(32), ((0, b'X'),)))
    assert untyped_entries.view(target, tmp_path, 'view.o') == target
    assert not (tmp_path/'view.o').exists()


def test_report_and_target_only_diff_use_the_same_split(tmp_path):
    if toolchains.installed('objdiff-cli') is None:
        pytest.skip('needs pinned objdiff')
    target = assemble(tmp_path)
    original = target.read_bytes()
    (tmp_path/'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': 'target.o'}]}))
    report = objdiff.report(tmp_path, tmp_path/'report.json')
    functions = report['units'][0]['functions']
    assert [(f['name'], int(f['size'])) for f in functions] == [
        ('func_80001000', 8), ('func_80001008', 8)]
    diff = objdiff.diff(target, None, 'func_80001008', cwd=tmp_path)
    assert diff.target_size == 8 and len(diff.rows) == 2
    assert target.read_bytes() == original


def test_report_and_project_diff_agree_when_candidate_object_exists(tmp_path):
    if toolchains.installed('objdiff-cli') is None:
        pytest.skip('needs pinned objdiff')
    target = assemble(tmp_path)
    view = untyped_entries.view(target, tmp_path, 'base.o')
    assert view != target
    (tmp_path/'objdiff.json').write_text(json.dumps({'units': [
        {'name': 'unit', 'target_path': 'target.o', 'base_path': 'base.o'}]}))
    report = objdiff.report(tmp_path, tmp_path/'report.json')
    functions = report['units'][0]['functions']
    assert [(f['name'], int(f['size'])) for f in functions] == [
        ('func_80001000', 8), ('func_80001008', 8)]
    diff = objdiff.diff(target, view, 'func_80001008', cwd=tmp_path, project=tmp_path, unit='unit')
    assert diff.target_size == 8 and diff.match_percent == 100


@pytest.mark.parametrize('unit_key', ['units', 'objects'])
def test_report_preserves_project_target_directory(tmp_path, unit_key):
    if toolchains.installed('objdiff-cli') is None:
        pytest.skip('needs pinned objdiff')
    target_dir = tmp_path/'targets'
    target_dir.mkdir()
    assemble(target_dir)
    (tmp_path/'objdiff.json').write_text(json.dumps({'target_dir': 'targets',
                                                    unit_key: [{'name': 'unit', 'path': 'target.o'}]}))
    report = objdiff.report(tmp_path, tmp_path/'report.json')
    assert [f['name'] for f in report['units'][0]['functions']] == ['func_80001000', 'func_80001008']


def test_project_sync_registers_both_functions(tmp_path):
    if toolchains.installed('objdiff-cli') is None:
        pytest.skip('needs pinned objdiff')
    from decompnet.app.engine import RomNetwork

    root = tmp_path/'project'
    root.mkdir()
    target = assemble(root)
    (root/'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': 'target.o'}]}))
    (root/'.decomp-wtf').mkdir()
    net = RomNetwork(tmp_path/'decomp.db', tmp_path/'home')
    net.forks._register('p', 'P', 'fork', root,
                        {'kind': 'fork', 'platform': 'n64', 'fork_kind': 'objdiff', 'ready': False}, 'test')
    net.forks.sync('p', log=lambda *_: None)
    names = {row['name'] for row in net.project_store(net.project_row('p')['id']).all(
        'SELECT name FROM functions WHERE project_id=?', net.project_row('p')['id'])}
    assert names == {'func_80001000', 'func_80001008'}
    diff = net.forks.get('p')[1].diff(net.forks.get('p')[1].unit('unit'), 'func_80001008')
    assert diff.target_size == 8
    assert symbols(target)['func_80001008'][0] == 'STT_NOTYPE'
    untyped_entries.view(target, root, 'base.o')
    (root/'unit.c').write_text('void func_80001000(void) {}\nvoid func_80001008(void) {}\n')
    (root/'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': 'target.o',
                                                           'base_path': 'base.o',
                                                           'metadata': {'source_path': 'unit.c'}}]}))
    net.forks.sync('p', log=lambda *_: None)
    row = net.project_store(net.project_row('p')['id']).one(
        "SELECT status FROM functions WHERE project_id=? AND name='func_80001008'", net.project_row('p')['id'])
    assert row['status'] == 'matched'
