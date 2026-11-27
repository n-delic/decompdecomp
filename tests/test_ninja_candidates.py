"""Generated C uses a project's Ninja rule without editing its build graph."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from decompnet.forks.project import ForkProject
from decompnet.forks.scaffold import prepare


@pytest.mark.skipif(shutil.which('ninja') is None and not (Path(sys.executable).parent / 'ninja').is_file(),
                    reason='needs ninja')
@pytest.mark.parametrize('rule', ['compile_c', 'cc'])
def test_candidate_reuses_existing_ninja_rule_and_nested_program_assembly(tmp_path, rule):
    root = tmp_path / 'project with spaces'
    root.mkdir()
    manifest = root / 'build.ninja'
    manifest.write_text(f'rule {rule}\n  command = cp $in $out\nbuild existing.o: {rule} existing.c\n')
    original = manifest.read_bytes()
    (root / 'existing.c').write_text('existing')
    target = root / '.decomp-wtf/verified-program-references-example/game/objects/abc.o'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'verified reference')
    assembly = root / 'asm/game/abc.s'
    assembly.parent.mkdir(parents=True)
    assembly.write_text('glabel demo\njr $ra\nnop\n')
    (root / 'objdiff.json').write_text(json.dumps({'custom_make': 'ninja', 'units': [{
        'name': 'game/asm/abc', 'target_path': target.relative_to(root).as_posix()}]}))
    fp = ForkProject(root)
    made = prepare(fp)
    assert made == ['src/decompdecomp/game/abc.c']
    unit = fp.unit('game/asm/abc')
    assert unit.base.read_bytes() == (root / unit.source).read_bytes()
    assert fp.objdiff_config()['units'][0]['metadata']['draft_assembly'] == 'asm/game/abc.s'
    assert manifest.read_bytes() == original
    # Later edits still build through the same adapter.
    (root / unit.source).write_text('changed candidate')
    assert fp.build_unit(unit)[0]
    assert unit.base.read_text() == 'changed candidate'


def test_candidate_does_not_invent_a_ninja_compiler_rule(tmp_path):
    from decompnet.forks.ninja_candidates import build
    from decompnet.forks.project import Unit

    (tmp_path / 'build.ninja').write_text('rule link\n  command = true\n')
    unit = Unit('demo', None, tmp_path/'build/demo.o', 'demo.c', False, None, scaffold=True)
    assert build(ForkProject(tmp_path), unit, 'ninja', [], lambda *_: None, 10) is None


def test_common_c_edge_bindings_keep_dominant_project_defaults():
    from decompnet.forks.ninja_candidates import _common_bindings

    manifest = '''rule cc
  command = compiler --encoding $encoding $flags -o $out $in
build a.o: cc a.c
  encoding = Shift_JIS
  flags = -O2
build b.o: cc b.c
  encoding = Shift_JIS
  flags = -O2
build c.o: cc c.c
  encoding = Shift_JIS
  flags = -O2
build d.o: cc d.c
  encoding = Shift_JIS
  flags = -O2
build e.o: cc e.c
  encoding = Shift_JIS
  flags = -O3
'''
    assert _common_bindings(manifest, 'cc') == {'encoding': 'Shift_JIS', 'flags': '-O2'}
    assert _common_bindings(manifest.replace('  flags = -O2', '  flags = -g', 2), 'cc') == {'encoding': 'Shift_JIS'}


def test_generated_make_config_is_corrected_only_for_ninja_only_projects(tmp_path):
    from decompnet.forks.ninja_candidates import correct_generated_make

    (tmp_path / 'build.ninja').write_text('')
    cfg = {'custom_make': 'make', 'units': [{'target_path': '.decomp-wtf/objdiff-references/targets/a.o'}]}
    assert correct_generated_make(tmp_path, cfg)['custom_make'] == 'ninja'
    assert cfg['custom_make'] == 'make'
    assert correct_generated_make(tmp_path, {**cfg, 'custom_args': ['VERSION=us']})['custom_make'] == 'make'
    assert correct_generated_make(tmp_path, {**cfg, 'units': [{'target_path': 'external/a.o'}]})['custom_make'] == 'make'
    (tmp_path / 'Makefile').write_text('')
    assert correct_generated_make(tmp_path, cfg)['custom_make'] == 'make'


@pytest.mark.skipif(shutil.which('ninja') is None and not (Path(sys.executable).parent / 'ninja').is_file(),
                    reason='needs ninja')
def test_candidate_never_accepts_stale_object_without_a_ninja_rule(tmp_path):
    from decompnet.forks.project import Unit

    (tmp_path / 'build.ninja').write_text('rule link\n  command = true\n')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'custom_make': 'ninja', 'units': []}))
    source = tmp_path / 'demo.c'
    source.write_text('new source')
    obj = tmp_path / 'demo.o'
    obj.write_text('stale object')
    unit = Unit('demo', None, obj, 'demo.c', False, None, scaffold=True)
    ok, output = ForkProject(tmp_path).build_unit(unit, log=lambda *_: None)
    assert not ok and 'no usable C build rule' in output
    assert obj.read_text() == 'stale object'


@pytest.mark.skipif(shutil.which('ninja') is None and not (Path(sys.executable).parent / 'ninja').is_file(),
                    reason='needs ninja')
def test_candidate_uses_project_rule_when_it_is_added_to_ninja_graph(tmp_path):
    from decompnet.forks.project import Unit

    (tmp_path / 'build.ninja').write_text('rule cc\n  command = cp $in $out\nbuild demo.o: cc demo.c\n')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'custom_make': 'ninja', 'units': []}))
    (tmp_path / 'demo.c').write_text('project source')
    unit = Unit('demo', None, tmp_path/'demo.o', 'demo.c', False, None, scaffold=True)
    ok, output = ForkProject(tmp_path).build_unit(unit, log=lambda *_: None)
    assert ok, output
    assert unit.base.read_text() == 'project source'


@pytest.mark.skipif(shutil.which('ninja') is None and not (Path(sys.executable).parent / 'ninja').is_file(),
                    reason='needs ninja')
def test_batched_candidates_do_not_accept_stale_objects(tmp_path):
    from decompnet.forks.project import Unit

    (tmp_path / 'build.ninja').write_text('rule link\n  command = true\n')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'custom_make': 'ninja', 'units': []}))
    units = []
    for name in ('one', 'two'):
        (tmp_path / f'{name}.c').write_text('new')
        (tmp_path / f'{name}.o').write_text('stale')
        units.append(Unit(name, None, tmp_path/f'{name}.o', f'{name}.c', False, None, scaffold=True))
    results = ForkProject(tmp_path).build_units(units, log=lambda *_: None)
    assert all(not result[0] for result in results)
