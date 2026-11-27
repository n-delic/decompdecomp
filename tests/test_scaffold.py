"""Only app-verified source-less units get probed C candidates, with rollback on unsupported builds."""

import json
from pathlib import Path

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
        assert unit.base.parent.is_dir()
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
                           _build_locks={1: nullcontext()}, net=SimpleNamespace(project_store=lambda _project:store))
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
                           _find=lambda text, names: ('demo', (0, len(text))), _known=lambda *a:'')
    fp = SimpleNamespace(root=tmp_path)
    unit = SimpleNamespace(scratch=None, name='asm/demo', source='candidate.c')
    result = list(forkengine.ForkEngine._autopilot_drafts(fake, {'arch':'mips'}, fp, {'platform': 'n64'}, {'name': 'demo'}, unit))
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
        assert unit.base.parent.is_dir()
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


def test_scaffold_accepts_a_symlinked_checkout_path(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    root.mkdir()
    project(root)
    link = tmp_path / 'link'
    link.symlink_to(root, target_is_directory=True)
    fp = ForkProject(link)
    def build(unit):
        unit.base.write_bytes(b'candidate')
        return True, ''
    monkeypatch.setattr(fp, 'build_unit', build)
    assert prepare(fp) == ['src/decompdecomp/demo.c']
    metadata = json.loads((root / 'objdiff.json').read_text())['units'][0]['metadata']
    assert metadata['draft_assembly'] == '.decomp-wtf/objdiff-references/asm/demo.s'


def test_scaffold_refuses_object_directory_symlink_outside_checkout(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    root.mkdir()
    fp = project(root)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'src/decompdecomp').mkdir(parents=True)
    existing = outside / 'src/decompdecomp/demo.c.o'
    existing.write_bytes(b'outside data')
    (root / 'build').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(fp, 'build_unit', lambda unit: pytest.fail('must not build outside the project'))
    with pytest.raises(ForkError, match='object path leaves'):
        prepare(fp)
    assert existing.read_bytes() == b'outside data'
    assert not (root / 'src/decompdecomp/demo.c').exists()


def test_scaffold_uses_preserved_assembly_after_project_clean(tmp_path, monkeypatch):
    ref = tmp_path / '.decomp-wtf/objdiff-references'
    target = ref / 'targets/asm/demo.s.o'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'verified')
    saved = ref / 'assembly/asm/demo.s'
    saved.parent.mkdir(parents=True)
    saved.write_text('glabel demo\njr $ra\nnop\n')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{
        'name': 'asm/demo', 'target_path': str(target.relative_to(tmp_path)),
        'metadata': {'assembly_source': 'asm/demo.s', 'draft_assembly': str(saved.relative_to(tmp_path))}}]}))
    fp = ForkProject(tmp_path)
    def build(unit):
        unit.base.write_bytes(b'compiled')
        return True, ''
    monkeypatch.setattr(fp, 'build_unit', build)
    assert len(prepare(fp)) == 1
    from decompnet.forks.drafts import find_asm
    assert find_asm(tmp_path, 'asm/demo', 'demo', None) == saved


def test_autopilot_prepares_every_batch_and_stop_keeps_completed_units(tmp_path, monkeypatch):
    import threading
    from contextlib import nullcontext
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine

    fp = project(tmp_path)
    config = json.loads((tmp_path / 'objdiff.json').read_text())
    ref = tmp_path / '.decomp-wtf/objdiff-references'
    for i in range(34):
        name = f'part{i:02d}'
        (ref / f'objects/{name}.o').write_bytes(b'reference')
        (ref / f'asm/{name}.s').write_text(f'glabel {name}\njr $ra\nnop\n')
        config['units'].append({'name': f'asm/{name}', 'target_path': f'.decomp-wtf/objdiff-references/objects/{name}.o'})
    (tmp_path / 'objdiff.json').write_text(json.dumps(config))
    def build(unit):
        unit.base.write_bytes(b'candidate')
        return True, ''
    monkeypatch.setattr(fp, 'build_unit', build)
    monkeypatch.setattr('decompnet.app.forkengine.toolchains.installed', lambda tool: {'id': tool})
    store = SimpleNamespace(all=lambda *args: [{'id': 17}])
    synced = []
    fake = SimpleNamespace(get=lambda slug: ({'id': 1}, fp, {'origin': 'local', 'platform': 'n64', 'ready': True}),
                           _tree=lambda proj: SimpleNamespace(exclusive=nullcontext), _build_locks={1: nullcontext()},
                           net=SimpleNamespace(project_store=lambda _: store), sync=lambda slug: synced.append(slug))
    stop = threading.Event()
    def progress(message, count=0):
        if count:
            stop.set()
    assert ForkEngine.autopilot_prepare(fake, 'demo', all_units=True, stop=stop, progress=progress) == [17]
    assert len(synced) == 1
    assert sum(bool(u.get('metadata', {}).get('autopilot_scaffold')) for u in fp.objdiff_config()['units']) == 32
    stop.clear()
    assert ForkEngine.autopilot_prepare(fake, 'demo', all_units=True, stop=stop, progress=lambda *args: None) == [17]
    assert len(synced) == 2
    assert sum(bool(u.get('metadata', {}).get('autopilot_scaffold')) for u in fp.objdiff_config()['units']) == 35


def test_scaffold_uses_the_project_versioned_object_directory(tmp_path, monkeypatch):
    fp = project(tmp_path)
    cfg = json.loads((tmp_path / 'objdiff.json').read_text())
    cfg['units'].insert(0, {'name': 'game/main', 'base_path': 'build/us/srcus/main.c.o',
                            'metadata': {'source_path': 'srcus/main.c'}})
    (tmp_path / 'objdiff.json').write_text(json.dumps(cfg))
    def build(unit):
        assert unit.base == tmp_path / 'build/us/src/decompdecomp/demo.c.o'
        unit.base.write_bytes(b'candidate')
        return True, ''
    monkeypatch.setattr(fp, 'build_unit', build)
    assert prepare(fp) == ['src/decompdecomp/demo.c']
    entry = fp.objdiff_config()['units'][1]
    assert entry['base_path'] == 'build/us/src/decompdecomp/demo.c.o'


def test_scaffold_does_not_guess_between_different_object_directories():
    from decompnet.forks.scaffold import _object_prefix

    cfg = {'units': [
        {'base_path': 'build/us/src/a.c.o', 'metadata': {'source_path': 'src/a.c'}},
        {'base_path': 'build/jp/src/b.c.o', 'metadata': {'source_path': 'src/b.c'}},
    ]}
    assert _object_prefix(cfg).as_posix() == 'build'
    assert _object_prefix({'units': [{'base_path': '../out/src/a.c.o',
                                      'metadata': {'source_path': 'src/a.c'}}]}).as_posix() == 'build'


def test_scaffold_uses_object_suffix_from_existing_c_units(tmp_path, monkeypatch):
    fp = project(tmp_path)
    cfg = json.loads((tmp_path / 'objdiff.json').read_text())
    cfg['units'].insert(0, {'name': 'main', 'base_path': 'build/us/src/main.o',
                            'metadata': {'source_path': 'src/main.c'}})
    (tmp_path / 'objdiff.json').write_text(json.dumps(cfg))
    def build(unit):
        assert unit.base == tmp_path / 'build/us/src/decompdecomp/demo.o'
        unit.base.write_bytes(b'candidate')
        return True, ''
    monkeypatch.setattr(fp, 'build_unit', build)
    assert prepare(fp) == ['src/decompdecomp/demo.c']
    assert fp.objdiff_config()['units'][1]['base_path'] == 'build/us/src/decompdecomp/demo.o'


def test_scaffold_ambiguous_object_suffix_falls_back_safely():
    from decompnet.forks.scaffold import _object_layout

    cfg = {'units': [
        {'base_path': 'build/src/a.o', 'metadata': {'source_path': 'src/a.c'}},
        {'base_path': 'build/src/b.c.o', 'metadata': {'source_path': 'src/b.c'}},
    ]}
    assert _object_layout(cfg) == (Path('build'), True)
