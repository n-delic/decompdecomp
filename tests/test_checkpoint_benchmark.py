import hashlib

import pytest

from decompnet import compile_timing
from decompnet.app import checkpoint_benchmark as bench
from decompnet.app import iterative_repairs


def test_independent_checkpoints_use_equal_budgets_and_require_exact_verdict():
    constructed = []
    def factory(case):
        cache = {}; constructed.append(cache)
        def evaluate(source):
            if source not in cache:
                compile_timing.add('requests',1);compile_timing.add('invocations',1)
                compile_timing.add('execute',.25)
                cache[source] = dict(score=0 if source=='zero' else 10,matched=False,outcome='scored')
            return cache[source]
        return evaluate,dict(scope='same-frozen-build')
    cases = [dict(id='early',source='a'),dict(id='later',source='b')]
    def generate(case,source,report):
        compile_timing.add('proposal_generation',.5)
        return [('family','zero')] if source in {'a','b'} else []
    report = bench.run(cases,factory,trials=1,seconds=1,generate=generate)
    assert len(constructed)==2 and constructed[0] is not constructed[1]
    assert report['recovered_checkpoints']==0  # score zero is not linked-byte proof
    assert report['model_tokens']==0
    for row in report['checkpoints']:
        assert row['compiler_invocations']==2
        assert row['timings']['compiler_execute_seconds']==.5
        assert row['timings']['proposal_generation_seconds']==.5
        assert row['family_coverage']['family']['tested']==1
    text = bench.markdown(report)
    assert '| early |' in text and 'not distinct functions' in text
    assert 'a' not in report and 'source' not in report['checkpoints'][0]


def test_unknown_compiler_instrumentation_is_not_reported_as_zero_cost():
    report = bench.run([dict(id='case',source='start')],
        lambda c:(lambda s:dict(score=10,matched=False,outcome='scored'),{}),
        trials=1,seconds=1,generate=lambda *a:[])
    assert report['checkpoints'][0]['compiler_invocations'] is None
    assert 'unknown' in bench.markdown(report)


@pytest.mark.parametrize('cases,trials,seconds', [([],1,1),([dict(id='a',source='s')]*2,1,1),
    ([dict(id='a',source='s')],True,1),([dict(id='a',source='s')],1,0)])
def test_invalid_suite_does_not_construct_a_judge(cases,trials,seconds):
    with pytest.raises(ValueError):
        bench.run(cases,lambda *a:pytest.fail('invalid suite'),trials=trials,seconds=seconds)


def test_changed_recorded_start_score_is_rejected():
    with pytest.raises(ValueError,match='recorded score'):
        bench.run([dict(id='a',source='s',expected_score=10)],
                  lambda c:(lambda s:dict(score=11,matched=False),{}),trials=1,seconds=1)


def test_cleanup_page_precedes_broad_generation_and_resumes_it_after_a_miss(monkeypatch):
    source = 'int f(int a){ int delta; do { delta=a & 1; } while(0); return delta; }'
    calls=[]
    monkeypatch.setattr(iterative_repairs,'targeted_pool',lambda *a,**k:calls.append(1) or [('other','candidate')])
    selection={}
    first=iterative_repairs.targeted_variants(source,{},dict(name='f',asm=''),'mips',[],
                                             tagged=True,selection=selection,limit=2)
    assert first[0][0]=='revisit_one_shot_scope' and first.more and not calls
    second=iterative_repairs.targeted_variants(source,{},dict(name='f',asm=''),'mips',[],
                                              tagged=True,selection=selection,limit=2)
    assert second==[('other','candidate')] and calls==[1]


def test_cli_frozen_hash_guard_runs_before_judge_or_output(tmp_path, monkeypatch):
    import json
    import sys
    header=tmp_path/'context.h';header.write_text('')
    source=tmp_path/'source.c';source.write_text('changed')
    manifest=tmp_path/'checkpoints.json';manifest.write_text(json.dumps(dict(
        header=dict(path='context.h',sha256=hashlib.sha256(b'').hexdigest()),
        checkpoints=[dict(id='a',path='source.c',sha256='old',expected_score=1)])))
    monkeypatch.setattr(sys,'argv',['replay','--project',str(tmp_path),'--function','f',
        '--checkpoints',str(manifest),'--output',str(tmp_path/'out.json')])
    monkeypatch.setattr(bench.linked_repair_benchmark,'evaluator',lambda *a:pytest.fail('changed source'))
    with pytest.raises(SystemExit):bench.main()
    assert not (tmp_path/'out.json').exists()


def test_cli_hashes_original_bytes_and_keeps_source_line_endings(tmp_path,monkeypatch):
    import json
    import sys
    raw=b'void f(void) {\r\n return;\r\n}\r\n'
    (tmp_path/'source.c').write_bytes(raw)
    (tmp_path/'context.h').write_bytes(b'')
    manifest=dict(header=dict(path='context.h',sha256=hashlib.sha256(b'').hexdigest()),
        checkpoints=[dict(id='a',path='source.c',sha256=hashlib.sha256(raw).hexdigest(),expected_score=1)])
    (tmp_path/'cases.json').write_text(json.dumps(manifest))
    captured=[]
    monkeypatch.setattr(bench,'run',lambda cases,*a,**k:captured.extend(cases) or {})
    monkeypatch.setattr(bench,'markdown',lambda report:'')
    monkeypatch.setattr(sys,'argv',['replay','--project',str(tmp_path),'--function','f',
        '--checkpoints',str(tmp_path/'cases.json'),'--output',str(tmp_path/'out.json')])
    bench.main()
    assert captured[0]['source'].encode()==raw
