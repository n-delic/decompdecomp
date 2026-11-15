import json
import re
from types import SimpleNamespace

import pytest

from decompnet.app import agent_evidence, agent_tokens, compiler_experiments, local_experiments, repair_generation
from decompnet.network import NetworkError
from tests.test_claims import _game


def game(tmp_path):
    net,pid=_game(tmp_path,[('f',10,'open'),('g',10,'open')])
    a,_=net.local_agent('worker-a');b,_=net.local_agent('worker-b')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=? WHERE name=?',('int f(){return 1;}',20,'f'))
    fn=net.claim_row(a,'game','f');_,rev=agent_tokens.candidate(net,a,'game','f')
    return net,a,b,fn,rev


def test_generation_deadline_retains_completed_stages_and_resumes_without_repeating(monkeypatch):
    net=SimpleNamespace();clock=[0];ran=[]
    monkeypatch.setattr(repair_generation.time,'monotonic',lambda:clock[0])
    def first(source):
        ran.append('first');clock[0]+=2;return [('a',source+'1')]
    def second(source):
        ran.append('second');return [('b',source+'2')]
    with repair_generation.session(net,'scope',1,['a','b']) as state:
        assert repair_generation.call(first,'C')
        assert not repair_generation.call(second,'C')
        assert state.incomplete
    with repair_generation.session(net,'scope',3,['a','b']):
        assert repair_generation.call(first,'C')
        assert repair_generation.call(second,'C')
    assert ran==['first','second']
    with repair_generation.session(net,'changed-header',4,['a']):repair_generation.call(first,'C')
    assert ran==['first','second','first']


def test_generation_goal_moves_past_already_tested_proposals():
    net=SimpleNamespace();seen=set();ran=[]
    def first():ran.append('first');return [('a','C1')]
    def second():ran.append('second');return [('b','C2')]
    accept=lambda family,source:source not in seen
    with repair_generation.session(net,'scope',None,['a','b'],accept=accept,goal=1) as state:
        repair_generation.call(first);assert not repair_generation.call(second)
        assert state.incomplete
    seen.add('C1')
    with repair_generation.session(net,'scope',None,['a','b'],accept=accept,goal=1):
        repair_generation.call(first);assert repair_generation.call(second)==[('b','C2')]
    assert ran==['first','second']


def test_generation_timeout_is_not_reported_as_exhaustion(tmp_path,monkeypatch):
    net,a,b,fn,rev=game(tmp_path)
    monkeypatch.setattr(agent_evidence,'preflight',lambda *a:None)
    def empty(*args,**kwargs):
        assert kwargs['deadline']
        pool=local_experiments.Pools();pool.incomplete=True;return pool
    monkeypatch.setattr(local_experiments,'available',empty)
    result=local_experiments.run(net,a,'game','f',rev)
    assert 'not exhaustion' in result and 'No untried' not in result


def test_routine_linker_chatter_is_hidden_but_raw_log_and_real_warning_survive(tmp_path):
    net,a,b,fn,rev=game(tmp_path)
    raw=':: Generating relocation HI16 BE\n.text::0000017a D_symbol\n:: Skipped for this pass\nwarning: relocation truncated for symbol\nuseful note'
    shown=agent_evidence.warning(net,a,fn,raw)
    assert 'Generating relocation' not in shown and 'Skipped for this pass' not in shown
    assert 'warning: relocation truncated' in shown and 'useful note' in shown
    assert raw in agent_evidence.warnings(net,a,fn)
    assert agent_evidence.warning(net,a,fn,':: Generating relocation HI16 BE\n.text::0000017a D_symbol')==''


def test_trials_survive_cache_loss_and_share_only_within_the_project_function(tmp_path,monkeypatch):
    net,a,b,fn,rev=game(tmp_path)
    monkeypatch.setattr(net,'_toolchain_named',lambda cfg,c:c)
    monkeypatch.setattr(net,'try_compiler',lambda *a,**kw:'score 10; diagnostic')
    result=compiler_experiments.run(net,a,'game','f',rev,[{'compiler':'fake','flags':'-O2'}])
    trial=re.search(r'trial_id=(\w+)',result)[1]
    net._compiler_trials.clear()
    shared=json.loads(compiler_experiments.retrieve(net,b,'game','f',trial))
    assert shared['source']=='int f(){return 1;}' and 'header' not in shared
    assert 'not a current match' in shared['verification']
    assert 'header' in json.loads(compiler_experiments.retrieve(net,b,'game','f',trial,True))
    with pytest.raises(NetworkError):compiler_experiments.retrieve(net,b,'game','g',trial)
    from decompnet.app.engine import RomNetwork
    reopened=RomNetwork(tmp_path/'app.db',tmp_path)
    assert json.loads(compiler_experiments.retrieve(reopened,b,'game','f',trial))['source']==shared['source']


def test_supporting_pages_are_bounded_and_editable_c_is_complete(tmp_path,monkeypatch):
    net,a,b,fn,rev=game(tmp_path)
    assert 'saved historical score' in agent_tokens.function_view(net,a,'game','f')
    raw='quality evidence\n'+'0123456789'*2000
    monkeypatch.setattr(agent_tokens,'_function_view',lambda *args:raw)
    first=agent_tokens.function_view(net,a,'game','f','quality',limit=1000)
    second=agent_tokens.function_view(net,a,'game','f','quality',start=1000,limit=1000)
    assert 'next start=1000' in first and first.endswith(raw[:1000])
    assert second.endswith(raw[1000:2000]) and 'partial code' in first
    assert agent_tokens.function_view(net,a,'game','f','candidate',limit=1000)==raw


def test_baseline_scores_are_labelled_without_asserting_a_regression(tmp_path):
    net,a,b,fn,rev=game(tmp_path)
    result=agent_evidence.baseline_note(fn,{'source':'int f(){return 2;}','score':40,'verified':True})
    assert 'current rechecked score 40' in result and 'historical best 20' in result
    assert 'different source' in result and 'no regression or match is inferred' in result


def test_baseline_header_and_compiler_changes_are_explicit(tmp_path):
    net,a,b,fn,rev=game(tmp_path)
    with net.store.tx() as db:
        attempt=db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,source,created_at) VALUES(?,?,20,1,?,0)',(fn['id'],a.id,fn['best_source'])).lastrowid
        db.execute('INSERT INTO repair_attempt_settings VALUES(?,?)',(attempt,json.dumps({'context_sha256':'old-header','compiler':{'toolchain':'old','flags':['-O1']}})))
    text=agent_evidence.baseline_note(fn,{'source':fn['best_source'],'score':40,'header_sha256':'new-header','compiler':{'toolchain':'new','flags':['-O2']}},net)
    assert 'same source' in text and 'Header differs' in text and 'Compiler profile differs' in text


def test_irrelevant_draft_refinement_is_not_run_for_an_explicit_family(monkeypatch):
    from decompnet.app import draft_repairs
    monkeypatch.setattr(draft_repairs.m2cfix,'refine_draft',lambda *a:pytest.fail('Unrequested m2c refinement'))
    with repair_generation.session(SimpleNamespace(),'scope',None,['inline_copy_loop']):
        draft_repairs.variants('int f(int arg0){return arg0;}','f','mips',limit=None)


def test_chatter_filter_preserves_linker_errors_and_removes_empty_heading():
    raw='compiler output:\n:: Generating relocation HI16 BE\n.text::0000017a D_symbol'
    assert agent_evidence.concise_warnings(raw)==''
    assert 'truncated' in agent_evidence.concise_warnings('.text::0000017a relocation truncated to fit')


def test_generators_reading_database_state_are_not_cached():
    net=SimpleNamespace(store=object());calls=[]
    def from_database(current):
        calls.append(1);return [('a',str(len(calls)))]
    for _ in range(2):
        with repair_generation.session(net,'scope',None,['a']):repair_generation.call(from_database,net)
    assert len(calls)==2
