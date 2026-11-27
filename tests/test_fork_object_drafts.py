"""Drafts use the reference object's assembled instructions when source assembly is not parseable."""
import json
import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.arch.mips_object_drafts import assembly
from decompnet.elfobj import FunctionObj
from decompnet.forks import drafts
from tests.mips_elf_fixture import linked_object


def _relocatable(name, code):
    raw = bytearray(linked_object(name, 0, code))
    raw[16:18] = (1).to_bytes(2, 'big')  # ET_REL
    return bytes(raw)


def _project(root: Path):
    code = bytes.fromhex('8c820000004510231440fffd0000000003e0000800000000')
    (root / 'target.o').write_bytes(_relocatable('wait', code))
    (root / 'original.s').write_text('LEAF(wait)\n1: lw $v0,0($a0)\nbnez $v0,1b\njr $ra\nEND(wait)\n')
    (root / 'source.c').write_text('/* candidate */\n')
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': 'target.o',
        'metadata': {'draft_assembly': 'original.s', 'source_path': 'source.c'}}]}))


def test_mips_object_assembly_preserves_delay_slots_and_local_branches():
    fn = FunctionObj('wait', 'mips', True, bytes.fromhex('8c820000004510231440fffd0000000003e0000800000000'))
    text = assembly(fn)
    assert text.startswith('glabel wait\n.L00000000:\n')
    assert 'bnez $v0, .L00000000\n    nop\n    jr $ra\n    nop' in text


def test_mips_object_assembly_declines_unresolved_control_flow():
    assert assembly(FunctionObj('jump', 'mips', True, bytes.fromhex('0800000000000000'))) is None
    assert assembly(FunctionObj('branch', 'mips', True, bytes.fromhex('1000000500000000'))) is None
    assert assembly(FunctionObj('truncated', 'mips', True, bytes.fromhex('03e00008'))) is None


def test_fork_draft_falls_back_to_reference_without_editing_it(tmp_path, monkeypatch):
    _project(tmp_path)
    original = (tmp_path / 'target.o').read_bytes()
    monkeypatch.setattr(drafts.toolchains, 'installed', lambda name: {'id': name})
    seen = []
    def run(text, name, target, **kwargs):
        seen.append(text)
        return 'void wait(void) {}' if text.startswith('glabel wait') else None
    monkeypatch.setattr(drafts.seeds, 'run_m2c', run)
    assert drafts.draft(tmp_path, 'n64', 'unit', 'wait', 'source.c', compiler_hint='gcc') == 'void wait(void) {}'
    assert len(seen) == 2 and 'LEAF(wait)' in seen[0]
    assert 'bnez $v0, .L00000000\n    nop\n    jr $ra\n    nop' in seen[1]
    assert (tmp_path / 'target.o').read_bytes() == original


def test_fork_draft_rejects_external_reference(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (tmp_path / 'outside.o').write_bytes(_relocatable('wait', bytes.fromhex('03e0000800000000')))
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'target_path': '../outside.o'}]}))
    assert drafts._object_assembly(root, 'n64', 'unit', 'wait') is None


def test_fork_draft_rejects_non_executable_reference(tmp_path):
    _project(tmp_path)
    obj = bytearray((tmp_path / 'target.o').read_bytes())
    section_table = int.from_bytes(obj[32:36], 'big')
    obj[section_table + 40 + 8:section_table + 40 + 12] = (2).to_bytes(4, 'big')
    (tmp_path / 'target.o').write_bytes(obj)
    assert drafts._object_assembly(tmp_path, 'n64', 'unit', 'wait') is None


def test_fork_draft_reads_target_directory_layout(tmp_path):
    _project(tmp_path)
    (tmp_path / 'targets').mkdir()
    (tmp_path / 'target.o').rename(tmp_path / 'targets' / 'unit.o')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'target_dir': 'targets', 'objects': [{'path': 'unit.o'}]}))
    assert 'glabel wait' in drafts._object_assembly(tmp_path, 'n64', 'unit.o', 'wait')


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_object_draft_preserves_symbol_relocations(tmp_path):
    from decompnet.elfobj import extract_function

    source = tmp_path / 'symbol.s'
    source.write_text('.text\n.set noreorder\n.globl read_value\n.ent read_value\nread_value:\n'
                      'lui $t0,%hi(value)\nlw $v0,%lo(value)($t0)\njr $ra\nnop\n.end read_value\n')
    obj = tmp_path / 'symbol.o'
    assembler = next(toolchains.path('binutils-mips').glob('*-as'))
    subprocess.run([str(assembler), '-EB', '-32', '-mips3', '-o', str(obj), str(source)],
                   capture_output=True, check=True)
    text = assembly(extract_function(obj.read_bytes(), 'read_value'))
    assert '%hi(value)' in text and '%lo(value)' in text


def _registered_project(tmp_path, monkeypatch):
    from decompnet.app.engine import RomNetwork
    from decompnet.forks.project import ForkProject

    root = tmp_path / 'project'
    root.mkdir()
    _project(root)
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    report = {'measures': {'total_functions': 1, 'matched_functions': 0, 'total_code': '24', 'matched_code': '0'},
              'units': [{'name': 'unit', 'functions': [{'name': 'wait', 'size': '24'}]}]}
    monkeypatch.setattr(ForkProject, 'report', lambda self: report)
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug: None)
    net.forks.sync('p', rep=report, log=lambda *_: None)
    proj, fp, cfg = net.forks.get('p')
    return net, proj, fp, cfg, fp.unit('unit')


def test_existing_successful_seed_is_migrated_without_regeneration(tmp_path, monkeypatch):
    net, proj, fp, cfg, unit = _registered_project(tmp_path, monkeypatch)
    fn = net._function('wait', 'p')
    old_key = net.forks._draft_key(cfg, unit, fp.root, object_revision=False)
    new_key = net.forks._draft_key(cfg, unit, fp.root)
    assert old_key != new_key
    with net.store.tx() as db:
        db.execute('UPDATE functions SET seed=?, seed_key=? WHERE id=?', ('existing draft', old_key, fn['id']))
    monkeypatch.setattr(drafts, 'draft', lambda *a, **k: pytest.fail('existing draft was regenerated'))
    assert net.forks._draft(proj, fp, cfg, net._function('wait', 'p'), unit) == 'existing draft'
    assert net._function('wait', 'p')['seed_key'] == new_key


@pytest.mark.parametrize('scored, expected', [(True, 'skip'), (False, 'fail')])
def test_new_object_fallback_retries_only_unscored_old_reports(tmp_path, monkeypatch, scored, expected):
    from decompnet.app import draft_reports

    net, proj, fp, cfg, unit = _registered_project(tmp_path, monkeypatch)
    fn = net._function('wait', 'p')
    legacy = (net.forks._draft_key(cfg, unit, fp.root, object_revision=False), cfg.get('draft_generation', 0))
    old_key = draft_reports.key(repr(legacy), net.forks._known(fp, unit), '', fn)
    previous = {'key': old_key, 'complete': True, 'scored': scored}
    monkeypatch.setattr(draft_reports, 'read', lambda *a, **k: previous)
    monkeypatch.setattr(draft_reports, 'save', lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(net.forks, '_autopilot_drafts', lambda *a, **k: calls.append(True) or iter(()))
    agent, _ = net.local_agent('object-draft-test')
    assert net.forks.autopilot_one(agent, proj, fn['id']) == expected
    assert bool(calls) is not scored


@pytest.mark.skipif(toolchains.installed('m2c') is None, reason='needs pinned m2c')
def test_real_m2c_drafts_scheduled_reference(tmp_path):
    _project(tmp_path)
    result = drafts.draft(tmp_path, 'n64', 'unit', 'wait', 'source.c', compiler_hint='gcc', valid=True)
    assert result and 'wait(' in result and 'while' in result
