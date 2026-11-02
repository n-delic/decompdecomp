"""Synthetic multi-family experiments share one revision and the normal judge in both modes."""
import json

import pytest

from decompnet.app import agent_tokens, local_experiments, tools
from decompnet.network import NetworkError
from tests.test_claims import _game


@pytest.mark.parametrize('mode',['rom','fork'])
def test_applicable_families_one_snapshot_and_budget(tmp_path,monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source='int f(){\nint a;\nshort b;\nreturn a+b;\n}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'compiler':{'toolchain':'example','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');fn=net._function('f','game');net._record(fn,a,10,source,1)
    seen=[]
    def compile(agent,function,text,slug):
        seen.append(text);net._record(fn,agent,20,text,1);return 'score 20'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    # This test substitutes the judge; baseline/build diagnostics have separate mode-specific coverage.
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight',lambda *args:None)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    args={'function':'f','revision':rev,'families':['signedness','declaration_order'],'max_trials':2}
    result=tools.call(net,a,'game','try_local_repairs',args)
    assert len(seen)==2 and '1: signedness, 2: declaration_order' in result, result
    assert 'starting candidate retained' in result
    assert agent_tokens.candidate(net,a,'game','f')[0]==source
    # Submitted local variants are remembered; a second call advances instead of replaying them.
    tools.call(net,a,'game','try_local_repairs',args)
    assert len(seen)==3 and len(set(seen))==3
    with pytest.raises(NetworkError,match='Candidate changed'):
        local_experiments.run(net,a,'game','f','stale',max_trials=2)
    assert len(seen)==3
    assert 'try_local_repairs' in tools.project_tools(net,'game')


@pytest.mark.parametrize('bad',[0,7,True,1.5])
def test_invalid_trial_budget_does_not_compile(tmp_path,bad):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with pytest.raises(NetworkError,match='max_trials'):
        local_experiments.run(net,a,'game','f','unused',max_trials=bad)


def test_release_handover_compacted_and_small_edit_coached_once(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    text='## CLAIMED: game/f\n### Target assembly\n```asm\n'+'nop\n'*100+'```\n'
    monkeypatch.setattr(net,'release_function',lambda *args:text)
    result=tools.call(net,a,'game','release_function',{'function':'f','notes':'done'})
    assert result.count('nop\n')==12 and 'get_assembly' in result
    net.claim_row(a,'game','f')
    source='int f(){\n/*'+'x'*500+'*/\nreturn 1;}'
    net._record(fn,a,10,source,1)
    def compile(agent,function,text,slug):
        net._record(fn,agent,10,text,1);return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    first=tools.call(net,a,'game','compile_and_diff',{'function':'f','source':source.replace('return 1','return 2')})
    second=tools.call(net,a,'game','compile_and_diff',{'function':'f','source':source.replace('return 1','return 3')})
    assert 'Small edit:' in first and 'Small edit:' not in second
    assert len(second)<250 and 'Candidate revision' in second


@pytest.mark.parametrize('mode',['rom','fork'])
def test_scalar_address_and_mask_families_available_in_both_modes(tmp_path,monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('solver')
    source='extern signed char state; int call(int,int); int f(int x){signed char v; v=state; state=call(x,v);return 0;}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'compiler':{'toolchain':'example','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');fn=net._function('f','game')
    monkeypatch.setattr(net.forks,'permuter_header',lambda *args:'')
    _,revision=agent_tokens.candidate(net,a,'game','f')
    pools=local_experiments.available(net,a,'game',fn,source,revision,['scalar_global_address','split_mask_stores'])
    assert len(pools['scalar_global_address'])==1
    source='void f(char *p){*(int *)(p+244)=(*(int *)(p+244)&0xFFEFFFFF)&0xFFFF7FFF;}'
    pools=local_experiments.available(net,a,'game',fn,source,revision,['split_mask_stores'])
    assert len(pools['split_mask_stores'])==2


@pytest.mark.parametrize('explicit',[False,True])
def test_automatic_pass_excludes_experimental_families_but_explicit_requests_work(tmp_path,monkeypatch,explicit):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('solver')
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    net.claim_row(a,'game','f');_,revision=agent_tokens.candidate(net,a,'game','f')
    monkeypatch.setattr(local_experiments.agent_evidence,'preflight',lambda *args:None)
    requested=[]
    def available(*args,**kwargs):
        requested.extend(args[-1]);return {}
    monkeypatch.setattr(local_experiments,'available',available)
    local_experiments.run(net,a,'game','f',revision,['experimental_bitfield_frame'] if explicit else None)
    assert ('experimental_bitfield_frame' in requested)==explicit
