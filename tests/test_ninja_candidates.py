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
def test_candidate_reuses_existing_ninja_rule_and_nested_program_assembly(tmp_path):
    root = tmp_path / 'project with spaces'
    root.mkdir()
    manifest = root / 'build.ninja'
    manifest.write_text('rule compile_c\n  command = cp $in $out\nbuild existing.o: compile_c existing.c\n')
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
