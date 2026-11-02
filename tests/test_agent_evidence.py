"""Dependency and quality evidence, availability counts and conservative loop liveness."""
import hashlib
import json

import pytest

from decompnet import candidate_quality, claim_stalls
from decompnet.app import dependency_guard, local_experiments, register_reuse_repairs, tools
from decompnet.arch.call_evidence import direct
from decompnet.network import NetworkError
from tests.test_claims import _game


@pytest.mark.parametrize('assembly',['jal helper','bl helper','callq helper','jmp helper','jal 0x80002000','blne helper','bl.w helper','b helper'])
def test_original_direct_call_operands_are_evidence(assembly):
    assert direct(assembly,['helper'],0x80002000)


@pytest.mark.parametrize('assembly',['# jal helper','/* jal helper */ nop','move helper, t0','jal other','call *%rax'])
def test_notes_data_and_indirect_calls_are_not_direct_evidence(assembly):
    assert not direct(assembly,['helper'],0x80002000)


def dependency_game(tmp_path):
    net,_=_game(tmp_path,[('caller',10,'open'),('helper',20,'open')]);a,_=net.local_agent('worker')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET asm='bl helper' WHERE name='caller'")
        db.execute('UPDATE functions SET best_source=?,best_score=100',('int helper(){return 1;}',))
    return net,a


def test_unsubstantiated_dependencies_leave_the_current_claim_intact(tmp_path):
    net,a=dependency_game(tmp_path);net.claim_row(a,'game','helper')
    with pytest.raises(NetworkError,match='no direct call'):
        net.release_function(a,'helper','Register allocation is different.','game',needs='caller')
    assert net.store.one('SELECT function_id FROM claims')['function_id']==net._function('helper','game')['id']
    assert not net.store.all('SELECT * FROM waits')


def test_dependency_pivots_cannot_bypass_exhaustion_and_only_run_once_per_input(tmp_path):
    net,a=dependency_game(tmp_path);caller=net._function('caller','game');need=net._function('helper','game')
    assert dependency_guard.permitted(net,caller,need)
    dependency_guard.remember(net,caller,need)
    assert not dependency_guard.permitted(net,caller,need)
    claim_stalls.released(net,need,exhausted=True)
    with pytest.raises(NetworkError,match='paused'):
        dependency_guard.validate(net,caller,need)
    with net.store.tx() as db: db.execute("UPDATE functions SET best_source='int helper(){return 2;}' WHERE name='helper'")
    assert dependency_guard.permitted(net,net._function('caller','game'),net._function('helper','game'))


@pytest.mark.parametrize('mode',['rom','fork'])
def test_structured_quality_issue_is_revision_scoped_and_preserves_its_reason(tmp_path,mode):
    net,a=dependency_game(tmp_path)
    with net.store.tx() as db: db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
    net.claim_row(a,'game','caller');fn=net._function('caller','game')
    bad='int caller(){int counter;return counter;}'
    good='int caller(){return 1;}'
    net._record(fn,a,10,bad,1);net._record(fn,a,20,good,1)
    revision=lambda s:hashlib.sha256(s.encode()).hexdigest()[:20]
    why='The historical draft overwrites the outer counter.'
    said=tools.call(net,a,'game','report_candidate_issue',dict(function='caller',revision=revision(bad),
                     issue='loop_mutation',reason=why,alternative_revision=revision(good)))
    assert 'hypothesis' in said
    fn=net._function('caller','game');record=candidate_quality.pending(net,fn)[0]
    assert record['structured'] and record['issue']=='loop_mutation' and record['warning']==why
    assert record['alternatives'] and fn['best_score']==10
    candidate_quality.capture(net,fn,a,'Historical candidate is incorrect.',None)
    assert candidate_quality.pending(net,fn)[0]['warning']==why
    from decompnet.app import agent_tokens
    before = net.store.one('SELECT COUNT(*) AS n FROM attempts')['n']
    restored = agent_tokens.get_candidate(net,a,'game','caller','reported_corrected')
    assert good in restored and 'historical score is not reverified' in restored
    assert agent_tokens.candidate(net,a,'game','caller')[0] == good
    assert net._function('caller','game')['best_score'] == 10
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n'] == before
    assert 'reported_corrected' in '\n'.join(candidate_quality.brief(net,fn))
    with pytest.raises(NetworkError,match='not saved'):
        candidate_quality.report(net,a,'game','caller','0'*20,'other','This is a concrete concern.')
    with pytest.raises(NetworkError,match='different, compiled'):
        candidate_quality.report(net,a,'game','caller',revision(bad),'other',why,revision(bad))


def test_quality_reports_require_a_claim_and_a_same_function_alternative(tmp_path):
    net,a=dependency_game(tmp_path);fn=net._function('caller','game')
    with pytest.raises(NetworkError,match='currently hold'):
        candidate_quality.report(net,a,'game','caller','0'*20,'other','This is a concrete concern.')
    net.claim_row(a,'game','caller')
    net._record(net._function('helper','game'),a,20,'int helper(){return 7;}',1)
    with pytest.raises(NetworkError,match='different, compiled'):
        candidate_quality.report(net,a,'game','caller',hashlib.sha256(fn['best_source'].encode()).hexdigest()[:20],
                                 'other','This is a concrete concern.',hashlib.sha256(b'int helper(){return 7;}').hexdigest()[:20])


def test_remaining_counts_use_the_runner_pool_cache_and_compile_history(tmp_path,monkeypatch):
    net,a=dependency_game(tmp_path);fn=net._function('caller','game');source='int caller(){return 1;}'
    seen=[]
    def pool(*args,**kwargs):
        seen.append(1);return {'first':['a','b'],'second':['b','c']}
    monkeypatch.setattr(local_experiments,'available',pool)
    text=local_experiments.summary(net,a,'game',fn,source,'revision')
    assert '3 distinct proposals' in text and 'first=2' in text
    assert text==local_experiments.summary(net,a,'game',fn,source,'revision') and len(seen)==1
    with net.store.tx() as db:
        db.execute('INSERT INTO agent_family_targets VALUES(?,?,?,?)',(fn['id'],'scope','compiled-miss','hash'))
    local_experiments.summary(net,a,'game',fn,source,'revision');assert len(seen)==2
    monkeypatch.setattr(local_experiments,'available',lambda *args,**kwargs:{})
    assert 'Do not retry' in local_experiments.summary(net,a,'game',fn,source,'changed')


@pytest.mark.parametrize('loop',[
    'for(i=0;i<n;i++){value=input[i];out[i]=value << 2;}',
    'while(i<n){value=input[i];out[i++]=value * 4;}',
    'do{value=input[i];out[i++]=value << 2;}while(i<n);',
    'for(i=0;i<n;i++){int value=2;out[i]=value << 2;}',
])
def test_final_reuse_inside_a_loop_requires_an_unconditional_reset(loop):
    declaration='' if 'int value=2' in loop else 'int value;'
    source='void f(int n,int *input,int *out){int i=0;'+declaration+loop+'}'
    assert any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


@pytest.mark.parametrize('loop',[
    'while(i<n){out[i++]=value << 2;}',
    'while(i<n){value=value+1;out[i++]=value << 2;}',
    'while(i<n){out[i]=value;value=input[i];out[i++]=value << 2;}',
    'while(i<n){if(input[i]){value=input[i];}out[i++]=value << 2;}',
    'while(value<n){value=input[i];out[i++]=value << 2;}',
    'while(i<n){value=input[i];while(i<n){out[i++]=value << 2;}}',
    'while(value << 2){out[i++]=3;}',
    'for(i=0;value << 2;i++){out[i]=3;}',
    'do{out[i++]=3;}while(value << 2);',
    'while(i<n)out[i++]=value << 2;',
])
def test_loop_carried_reads_conditions_conditional_resets_and_unknown_scopes_stay_blocked(loop):
    source='void f(int n,int *input,int *out){int i=0;int value=2;'+loop+'}'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


def test_parenthesized_address_escape_prevents_loop_register_reuse():
    source='void f(int n,int *out){int i;int value;int *alias=&((value));for(i=0;i<n;i++){value=i;out[i]=value << 2;}use(alias);}'
    assert not any(f=='last_shift_reuse' for f,_ in register_reuse_repairs.variants(source,'f'))


def test_resumed_session_includes_remaining_counts_without_resending_c(tmp_path,monkeypatch):
    from decompnet.app import agents
    net,a=dependency_game(tmp_path);net.claim_row(a,'game','caller')
    manager=object.__new__(agents.Manager);manager.net=net
    monkeypatch.setattr(manager,'_prompt',lambda s:'Task')
    monkeypatch.setattr(net,'overview',lambda slug:{'platform':'','kind':'rom','compiler':{'toolchain':'example'}})
    monkeypatch.setattr(tools,'up_front',lambda *args:'')
    monkeypatch.setattr(net,'instructions',lambda slug:'')
    monkeypatch.setattr(local_experiments,'available',lambda *args,**kwargs:{'signedness':['new candidate']})
    session=agents.Session('id','api','Worker','game',a.name,{})
    prompt=manager.prompt(session)
    assert 'caller: Remaining local repairs' in prompt and '1 distinct proposals' in prompt
    assert 'int helper(){return 1;}' not in prompt


def test_unknown_project_assembly_is_read_from_the_original_object(tmp_path,monkeypatch):
    from types import SimpleNamespace
    net,a=dependency_game(tmp_path)
    with net.store.tx() as db: db.execute("UPDATE functions SET asm='' WHERE name='caller'")
    monkeypatch.setattr(net,'fork_slug',lambda _: 'game')
    fp=SimpleNamespace(unit=lambda _:object(),diff=lambda *args:SimpleNamespace(target_asm=lambda:'bl helper'))
    monkeypatch.setattr(net.forks,'get',lambda _: (None,fp,{}))
    dependency_guard.validate(net,net._function('caller','game'),net._function('helper','game'))
