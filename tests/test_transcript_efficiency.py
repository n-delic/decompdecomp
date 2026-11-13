"""Broken baselines and repeated packets should not spend a batch or repeat all context."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import agent_tokens, draft_repairs, local_experiments, tools
from tests.test_claims import _game


@pytest.mark.parametrize('mode',['rom','fork'])
def test_broken_baseline_compiled_once_and_no_generated_variants_run(tmp_path,monkeypatch,mode):
    net,pid=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('solver')
    source='int f(){int a; return a;}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':'rom','compiler':{'toolchain':'example','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(?, 'a',1,'')",(pid,))
    net.claim_row(agent,'game','f');seen=[]
    if mode=='rom':
        monkeypatch.setattr(net,'_compile',lambda *args:seen.append(1) or SimpleNamespace(ok=False,log='parse error before ?'))
    else:
        monkeypatch.setattr(net,'fork_slug',lambda _: 'game')
        monkeypatch.setattr(net.forks,'get',lambda _: (None,SimpleNamespace(root=tmp_path,unit=lambda _:SimpleNamespace(source='source.c',target=None)),None))
        def failed_build(*args,**kwargs):
            seen.append(1)
            net._generation[pid]=net._generation.get(pid,0)+1
            return {'score':None,'text':'BUILD ERROR: missing type'}
        monkeypatch.setattr(net.forks,'_attempt',failed_build)
    monkeypatch.setattr(agent_tokens,'variants',lambda *a,**kw:pytest.fail('variants must not run'))
    _,revision=agent_tokens.candidate(net,agent,'game','f')
    for _ in range(2):
        assert 'Baseline blocked' in local_experiments.run(net,agent,'game','f',revision,['signedness'])
    assert seen==[1]
    assert net._function('f','game')['attempts']==0
    assert 'baseline blocked' in local_experiments.summary(net,agent,'game',net._function('f','game'),source,revision)
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(?, 'a',2,'typedef int word;')",(pid,))
    local_experiments.run(net,agent,'game','f',revision,['signedness'])
    assert seen==[1,1]


@pytest.mark.parametrize('mode',['rom','fork'])
def test_new_families_reuse_the_shared_generator_and_adaptive_order(tmp_path,monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('solver')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':{'toolchain':'example','flags':[]}}),))
    source='void f(void *p){*(int*)((char*)p+20)=1;use(*(int*)((char*)p+20));*(short*)((char*)p+24)=2;}'
    fn=net._function('f','game')
    monkeypatch.setattr(net,'_context',lambda *a: {'header':''})
    if mode=='fork':
        monkeypatch.setattr(net,'fork_slug',lambda _: 'game')
        monkeypatch.setattr(net.forks,'get',lambda _: (None,SimpleNamespace(root=tmp_path,unit=lambda _:SimpleNamespace(source='source.c',target=None)),None))
        monkeypatch.setattr(net.forks,'permuter_header',lambda *a:'')
    labels={};shared=draft_repairs.variants(source,'f','ppc',labels=labels,limit=None)
    expected={text for text in shared if labels[text]=='inferred_record_layout'}
    assert expected
    pools=local_experiments.available(net,agent,'game',fn,source,'revision',['inferred_record_layout'])
    assert set(pools['inferred_record_layout'])==expected
    assert set(draft_repairs.AGENT_FAMILIES)<=set(local_experiments.FAMILIES)


def test_repeated_claim_shrinks_only_after_normal_claiming_and_refreshes_after_change(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',60,'open')]);agent,_=net.local_agent('solver')
    monkeypatch.setattr(net,'_brief',lambda fn,*a,**kw:'## CLAIMED: game/f\n### Candidate\n```c\nint f(){return 1;}\n```\n')
    first=tools._claim(net,agent,'game','','',4)
    second=tools._claim(net,agent,'game','','',4)
    assert 'Requested 4 functions; returned 1' in first and 'batch limit' in first
    assert '```c' in first and 'unchanged claim' in second and '```c' not in second
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==1
    net.release(agent,'f','','game')
    assert '```c' in tools._claim(net,agent,'game','f','',1)


def test_compact_project_facts_keep_progress_and_do_not_list_every_range(monkeypatch):
    ranges=[{'start':i,'end':i+1,'name':'Compiler','flags':['-O2']} for i in range(100)]
    ov={'title':'Demo','rom':{'game_code':'TEST','region':'Europe'},'compiler':{'name':'Compiler','flags':[]},
        'compiler_ranges':ranges,'compilers':['Compiler'],'matched_bytes':20,'bytes':100,
        'matched':2,'functions':10,'attempts':30,'context_version':1,'suggestions':[]}
    net=SimpleNamespace(_project=lambda _: {'id':1,'name':'Demo','slug':'game'}, claim_order=lambda _:None, cfg=lambda _: {'platform':'n64','compiler':{'toolchain':'gcc-2.7.2-kmc'}}, project_store=lambda _: SimpleNamespace(one=lambda *a: {'matched':2,'total':10,'attempts':30}), instructions=lambda _:'Keep this instruction.',overview=lambda _:ov,fork_slug=lambda _:None,
                        knowledge=SimpleNamespace(for_prompt=lambda _:''))
    compact=tools.project_info(net,'game')
    assert 'Compact saved metadata' in compact and '2/10 functions' in compact
    assert '0x0-0x1' not in compact and 'Keep this instruction.' in compact
    assert '0x0-0x1' in tools.project_info(net,'game',detail=True)


def test_agent_local_policy_records_measured_trials_but_not_cached_replays(tmp_path,monkeypatch):
    from decompnet.app import agent_evidence
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('solver')
    source='int f(){return 1;}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':{'toolchain':'example','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(agent,'game','f');fn=net._function('f','game')
    _,revision=agent_tokens.candidate(net,agent,'game','f')
    baseline={'source':source,'score':10,'rows':[],'verified':True,'exact':False}
    monkeypatch.setattr(agent_evidence,'preflight',lambda *a:baseline)
    monkeypatch.setattr(local_experiments,'available',lambda *a,**kw: {'signedness':['one'],'declaration_order':['two']})
    def variants(*args,**kwargs):
        net._local_trial_observations={(agent.id,fn['id']):[
            {'score':20,'rows':[],'cached':False,'elapsed_ms':5},
            {'score':20,'rows':[],'cached':True,'elapsed_ms':0}]}
        return 'measured'
    monkeypatch.setattr(agent_tokens,'variants',variants)
    local_experiments.run(net,agent,'game','f',revision,['signedness','declaration_order'],2)
    history=net.store.all('SELECT family,attempted FROM repair_policy')
    assert [(r['family'],r['attempted']) for r in history]==[('signedness',1)]


def test_explicit_authored_patch_batch_can_repair_a_broken_start(tmp_path,monkeypatch):
    from decompnet.app import agent_evidence
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('solver')
    source='int f(){return ?;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(agent,'game','f');_,revision=agent_tokens.candidate(net,agent,'game','f')
    monkeypatch.setattr(agent_evidence,'preflight',lambda *a:pytest.fail('authored corrections must remain possible'))
    seen=[]
    monkeypatch.setattr(tools,'_compile',lambda n,a,s,f,text:seen.append(text) or 'compile error')
    agent_tokens.variants(net,agent,'game','f',revision,[{'old':'?','new':'0'}])
    assert seen==['int f(){return 0;}']
