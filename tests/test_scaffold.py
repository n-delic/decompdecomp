"""Only app-verified source-less units get probed C candidates, with rollback on unsupported builds."""

import json

import pytest

from decompnet.forks.project import ForkError, ForkProject
from decompnet.forks.scaffold import prepare


def project(root):
    ref = root / '.decomp-wtf/objdiff-references'
    (ref / 'objects').mkdir(parents=True)
    (ref / 'asm').mkdir()
    (ref / 'objects/demo.o').write_bytes(b'verified reference')
    (ref / 'asm/demo.s').write_text('glabel demo\njr $ra\nnop\n')
    cfg = {'units': [{'name': 'asm/demo', 'target_path': '.decomp-wtf/objdiff-references/objects/demo.o'}]}
    (root / 'objdiff.json').write_text(json.dumps(cfg))
    return ForkProject(root)


def test_scaffold_probes_real_object_rule_and_preserves_reference(tmp_path, monkeypatch):
    fp = project(tmp_path)
    def build(unit):
        assert unit.source == 'src/decompdecomp/demo.c'
        unit.base.parent.mkdir(parents=True)
        unit.base.write_bytes(b'compiled empty candidate')
        return True, 'ok'
    monkeypatch.setattr(fp, 'build_unit', build)
    assert prepare(fp) == ['src/decompdecomp/demo.c']
    assert prepare(fp) == []
    entry = json.loads((tmp_path / 'objdiff.json').read_text())['units'][0]
    assert entry['base_path'] == 'build/src/decompdecomp/demo.c.o'
    assert entry['metadata']['draft_assembly'] == '.decomp-wtf/objdiff-references/asm/demo.s'
    unit = fp.units()[0]
    assert unit.target.read_bytes() == b'verified reference'
    assert unit.base.read_bytes() == b'compiled empty candidate'
    assert 'M2C_DEFINES' in (tmp_path / unit.source).read_text()


@pytest.mark.parametrize('failure', ['failed', 'missing_object'])
def test_failed_probe_rolls_back_sources_and_config(tmp_path, monkeypatch, failure):
    fp = project(tmp_path)
    original = (tmp_path / 'objdiff.json').read_bytes()
    monkeypatch.setattr(fp, 'build_unit', lambda unit: (failure != 'failed', 'no supported rule'))
    with pytest.raises(ForkError, match='cannot compile'):
        prepare(fp)
    assert (tmp_path / 'objdiff.json').read_bytes() == original
    assert not (tmp_path / 'src/decompdecomp/demo.c').exists()


def test_unrelated_target_and_existing_user_file_are_retained(tmp_path):
    fp = project(tmp_path)
    source = tmp_path / 'src/decompdecomp/demo.c'
    source.parent.mkdir(parents=True)
    source.write_text('user code')
    with pytest.raises(ForkError, match='already exists'):
        prepare(fp)
    assert source.read_text() == 'user code'
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'other', 'target_path': 'expected/other.o'}]}))
    assert prepare(fp) == []


def test_imported_project_can_prepare_autopilot_without_origin_gate(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine
    from decompnet.forks import scaffold

    fp = project(tmp_path)
    monkeypatch.setattr(scaffold, 'prepare', lambda project: [])
    monkeypatch.setattr('decompnet.app.forkengine.toolchains.installed', lambda tool: {'id': tool})
    store = SimpleNamespace(all=lambda *args: [{'id': 17}])
    fake = SimpleNamespace(get=lambda slug: ({'id': 1}, fp, {'origin': 'local', 'platform': 'n64', 'ready': True}),
                           _tree=lambda proj: SimpleNamespace(exclusive=nullcontext),
                           _build_locks={1: nullcontext()}, net=SimpleNamespace(store=store))
    assert ForkEngine.autopilot_prepare(fake, 'demo') == [17]


def test_upstream_draft_pipeline_keeps_imported_compiler_hint(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import forkengine

    source = tmp_path / 'candidate.c'
    source.write_text('')
    code = 'void demo(void) {}'
    seen = []
    def draft(*args, **kwargs):
        seen.append(kwargs['compiler_hint'])
        return code
    monkeypatch.setattr(forkengine.drafts, 'draft', draft)
    monkeypatch.setattr(forkengine.compiler_info, 'describe', lambda *a: {'name': 'IDO 5.3'})
    fake = SimpleNamespace(_meta=lambda fn: {'symbol': 'demo'}, _names=lambda meta, fn: ['demo'],
                           _find=lambda text, names: ('demo', (0, len(text))))
    fp = SimpleNamespace(root=tmp_path)
    unit = SimpleNamespace(scratch=None, name='asm/demo', source='candidate.c')
    result = list(forkengine.ForkEngine._autopilot_drafts(fake, {}, fp, {'platform': 'n64'}, {'name': 'demo'}, unit))
    assert seen == ['IDO 5.3']
    assert len(result) == 1 and 'void demo(void)' in result[0][1]


def test_rebuilt_targets_use_original_assembly_for_candidates_and_drafts(tmp_path, monkeypatch):
    from decompnet.forks.drafts import find_asm

    ref = tmp_path / '.decomp-wtf/objdiff-references/targets/asm'
    ref.mkdir(parents=True)
    (ref / 'demo.s.o').write_bytes(b'verified linked reference')
    assembly = tmp_path / 'edition/asm/demo.s'
    assembly.parent.mkdir(parents=True)
    assembly.write_text('glabel demo\njr $ra\nnop\n')
    cfg = {'units': [{'name': 'edition/asm/demo',
                     'target_path': '.decomp-wtf/objdiff-references/targets/asm/demo.s.o',
                     'metadata': {'assembly_source': 'edition/asm/demo.s'}}]}
    (tmp_path / 'objdiff.json').write_text(json.dumps(cfg))
    fp = ForkProject(tmp_path)
    def build(unit):
        unit.base.parent.mkdir(parents=True)
        unit.base.write_bytes(b'empty compiled candidate')
        return True, 'ok'
    monkeypatch.setattr(fp, 'build_unit', build)
    made = prepare(fp)
    assert len(made) == 1
    assert made[0].startswith('src/decompdecomp/candidates/demo_')
    assert fp.safe_path(made[0], write=True).is_file()
    assert find_asm(tmp_path, 'edition/asm/demo', 'demo', None) == assembly
    assert (ref / 'demo.s.o').read_bytes() == b'verified linked reference'
    assert assembly.read_text().startswith('glabel demo')


def test_scaffold_batches_continue_without_replacing_existing_sources(tmp_path, monkeypatch):
    fp = project(tmp_path)
    cfg = json.loads((tmp_path / 'objdiff.json').read_text())
    entry = dict(cfg['units'][0], name='asm/other',
                 target_path='.decomp-wtf/objdiff-references/objects/other.o')
    cfg['units'].append(entry)
    ref = tmp_path / '.decomp-wtf/objdiff-references'
    (ref / 'objects/other.o').write_bytes(b'other reference')
    (ref / 'asm/other.s').write_text('glabel other\njr $ra\nnop\n')
    (tmp_path / 'objdiff.json').write_text(json.dumps(cfg))
    def build(unit):
        unit.base.parent.mkdir(parents=True, exist_ok=True)
        unit.base.write_bytes(b'empty candidate')
        return True, 'ok'
    monkeypatch.setattr(fp, 'build_unit', build)
    assert prepare(fp, limit=1) == ['src/decompdecomp/demo.c']
    assert prepare(fp, limit=1) == ['src/decompdecomp/other.c']
    assert prepare(fp, limit=1) == []


def test_draft_assembly_metadata_cannot_escape_checkout(tmp_path):
    from decompnet.forks.drafts import find_asm

    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [
        {'name': 'demo', 'metadata': {'draft_assembly': '../outside.s'}}]}))
    assert find_asm(tmp_path, 'demo', 'demo', None) is None


def test_numeric_mips_registers_are_normalized_without_changing_data_or_coprocessors():
    from decompnet.arch.mips import abi_register_names

    text = ('glabel demo\n addiu $2, $4, 1 # keep $31 in comments\n jr $31\n'
            ' mfc0 $2, $12\n ctc1 $4, $31\n add.s $f1, $f2, $f3\n .ascii "$31"\n')
    assert abi_register_names(text) == (
        'glabel demo\n addiu $v0, $a0, 1 # keep $31 in comments\n jr $ra\n'
        ' mfc0 $v0, $12\n ctc1 $a0, $31\n add.s $f1, $f2, $f3\n .ascii "$31"\n')


def test_numeric_return_register_produces_a_real_machine_draft():
    from decompnet import seeds

    if not seeds.available():
        pytest.skip('m2c is not installed')
    text = '.text\nglabel demo\n addiu $2, $4, 1\n jr $31\n nop\n'
    draft = seeds.run_m2c(text, 'demo', 'mips-gcc-c', valid=True, strict=True)
    assert draft and 'demo(' in draft and 'return' in draft


def test_native_assembly_unit_probes_c_without_replacing_linked_baseline(tmp_path, monkeypatch):
    (tmp_path / 'asm').mkdir()
    assembly = tmp_path / 'asm/demo.s'
    assembly.write_text('glabel demo\njr $ra\nnop\n')
    (tmp_path / 'expected').mkdir()
    (tmp_path / 'expected/demo.o').write_bytes(b'original target')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{
        'name': 'asm/demo', 'target_path': 'expected/demo.o', 'base_path': 'build/asm/demo.o',
        'metadata': {'source_path': 'asm/demo.s'}}]}))
    fp = ForkProject(tmp_path)
    def build(unit):
        unit.base.parent.mkdir(parents=True, exist_ok=True)
        unit.base.write_bytes(b'candidate')
        return True, ''
    monkeypatch.setattr(fp, 'build_unit', build)
    made = prepare(fp)
    assert len(made) == 1
    entry = fp.objdiff_config()['units'][0]
    assert entry['metadata']['draft_assembly'] == 'asm/demo.s'
    assert entry['target_path'] == 'expected/demo.o'
    assert assembly.read_text() == 'glabel demo\njr $ra\nnop\n'
    assert (tmp_path / 'expected/demo.o').read_bytes() == b'original target'


def test_scaffold_only_probes_requested_units(tmp_path, monkeypatch):
    fp = project(tmp_path)
    monkeypatch.setattr(fp, 'build_unit', lambda unit: pytest.fail('unrelated unit was probed'))
    assert prepare(fp, units={'other'}) == []
