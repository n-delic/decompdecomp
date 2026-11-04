"""Frozen private inputs invalidate before replay when compilation evidence changes."""
import json

import pytest

from decompnet.app import frozen_repair_cases


def test_freeze_and_validate_all_sources_headers_and_compiler_scopes(tmp_path):
    manifest=tmp_path/'cases.json'
    for name in ('first','second'):
        (tmp_path/f'{name}.c').write_text(f'int {name}(void){{return 3;}}')
    header=tmp_path/'context.h';header.write_text('int global;')
    manifest.write_text(json.dumps(dict(cases=[dict(name=n,before=f'{n}.c',header='context.h') for n in ('first','second')])))
    scope=dict(target='one',compiler='pinned')
    def evaluator(*a):return None,scope.copy(),None,None,None
    cases,frozen=frozen_repair_cases.load(manifest,tmp_path,evaluator,freeze=True)
    assert len(cases)==2 and all('source' not in c for c in frozen['cases'])
    manifest.write_text(json.dumps(frozen))
    assert frozen_repair_cases.load(manifest,tmp_path,evaluator)[0]==cases
    scope['compiler']='changed'
    with pytest.raises(ValueError,match='compiler changed'):frozen_repair_cases.load(manifest,tmp_path,evaluator)
    scope['compiler']='pinned'
    header.write_text('int other;')
    with pytest.raises(ValueError,match='header changed'):frozen_repair_cases.load(manifest,tmp_path,evaluator)
    header.write_text('int global;')
    (tmp_path/'second.c').write_text('changed')
    with pytest.raises(ValueError,match='source or header changed'):frozen_repair_cases.load(manifest,tmp_path,evaluator)
    frozen['cases'][1]=frozen['cases'][0]
    manifest.write_text(json.dumps(frozen))
    with pytest.raises(ValueError,match='unique'):frozen_repair_cases.load(manifest,tmp_path,evaluator)


def test_private_frozen_suite_when_supplied():
    import os
    from pathlib import Path

    from decompnet.app import linked_repair_benchmark as bench
    location=os.environ.get('DECOMPNET_FROZEN_REPAIR_SUITE')
    if not location:pytest.skip('Supply a private frozen manifest; no game data in CI.')
    root=Path(os.environ['DECOMPNET_FROZEN_REPAIR_PROJECT'])
    cases,_=frozen_repair_cases.load(Path(location),root,bench.evaluator)
    report=bench.suite(root,cases,trials=20,seconds=30)
    assert report['functions']==len(cases) and report['candidate_compiles']<=20*len(cases)
    assert all(not c['baseline_exact'] for c in report['cases'])
    assert all(not case.get('expect_recovery') or result['first_exact_trial'] is not None
               for case,result in zip(cases,report['cases']))


def test_distinct_precursors_of_one_function_can_share_a_verified_exact_control(tmp_path):
    manifest=tmp_path/'cases.json'
    (tmp_path/'context.h').write_text('')
    (tmp_path/'exact.c').write_text('exact')
    (tmp_path/'a.c').write_text('before a')
    (tmp_path/'b.c').write_text('before b')
    manifest.write_text(json.dumps({'cases':[dict(name='f',id=n,before=n+'.c',header='context.h',control='exact.c',expect_recovery=True) for n in ['a','b']]}))
    def evaluator(*a):return lambda s:dict(matched=s=='exact'),{'compiler':'pinned'},None,None,None
    cases,frozen=frozen_repair_cases.load(manifest,tmp_path,evaluator,freeze=True)
    assert [c['id'] for c in cases]==['a','b']
    assert all(c['expect_recovery'] for c in cases)
    manifest.write_text(json.dumps(frozen))
    assert frozen_repair_cases.load(manifest,tmp_path,evaluator)[0]==cases
    (tmp_path/'exact.c').write_text('changed')
    with pytest.raises(ValueError,match='exact control changed'):
        frozen_repair_cases.load(manifest,tmp_path,evaluator)
    with pytest.raises(ValueError,match='no longer passes'):
        frozen_repair_cases.load(manifest,tmp_path,evaluator,freeze=True)
