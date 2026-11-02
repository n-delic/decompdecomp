import json

from decompnet.app import agent_local_pass, agent_tokens, tools
from tests.test_claims import _game
from tests.test_rom import built  # noqa: F401


def test_pass_is_opt_in_bounded_and_returns_selected_revision_without_recursive_tools(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('solver')
    source='int f(){return 1;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=10',(source,))
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':'rom'}),))
    net.claim_row(agent,'game','f');fn=net._function('f','game')
    net._agent_outcomes={(agent.id,fn['id']):{'source':source,'score':10,'rows':[]}}
    seen=[]
    def run(net,a,slug,function,revision,**kwargs):
        seen.append(kwargs)
        net._record(fn,a,5,'int f(){return 2;}',0)
        agent_tokens.outcome(net,a,fn,'int f(){return 2;}','score 5')
    monkeypatch.setattr(agent_local_pass.local_experiments,'run',run)
    text='score 10\nBudget: old; Candidate revision old.'
    assert agent_local_pass.after_tool(net,agent,'game','compile_patch',{'function':'f'},text)==text
    agent_local_pass.configure(net,{'agent_local_enabled':True,'agent_local_trials':2,'agent_local_seconds':3})
    result=agent_local_pass.after_tool(net,agent,'game','compile_patch',{'function':'f'},text)
    assert len(seen)==1 and seen[0]['max_trials']==2 and seen[0]['seconds']==3
    assert 'return 2' in result and 'selected an improvement' in result and 'Candidate revision old' not in result
    assert 'automatic agent local pass'==seen[0]['provenance_kind']
    assert agent_local_pass.after_tool(net,agent,'game','try_local_repairs',{'function':'f'},text)==text


def test_shared_dispatch_runs_pass_for_api_and_mcp_entry_points(monkeypatch):
    from decompnet.app import tool_policy

    monkeypatch.setattr(tool_policy, 'record', lambda *a, **kw: None)
    seen=[]
    monkeypatch.setitem(tools.HANDLERS,'project_info',lambda *a,**kw:'facts')
    monkeypatch.setattr(agent_local_pass,'after_tool',lambda *args:seen.append(args[3]) or args[-1])
    assert tools._dispatch(None,None,'game','project_info',{})=='facts'
    assert seen==['project_info']


def test_generated_trials_share_the_stall_budget_and_skip_closed_or_wide_misses(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('solver')
    source='int f(){return 1;}'
    net.claim_row(a,'game','f');fn=net._function('f','game')
    net._agent_outcomes={(a.id,fn['id']):{'source':source,'score':1000}}
    agent_local_pass.configure(net,{'agent_local_enabled':True})
    def unexpected(*args,**kwargs):raise AssertionError('must not run a local search')
    monkeypatch.setattr(agent_local_pass.local_experiments,'run',unexpected)
    assert agent_local_pass.after_tool(net,a,'game','compile_and_diff',{'function':'f'},'score 1000')=='score 1000'
    net._agent_outcomes[a.id,fn['id']]['score']=1
    monkeypatch.setattr(net,'attempt_limit_note',lambda *args:'budget exhausted')
    assert agent_local_pass.after_tool(net,a,'game','compile_patch',{'function':'f'},'score 1')=='score 1'


def test_time_exhausted_during_baseline_does_not_submit_a_variant(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('solver');source='int f(){return 1;}'
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');_,revision=agent_tokens.candidate(net,a,'game','f')
    from types import SimpleNamespace
    times=iter([0,6])
    monkeypatch.setattr(agent_local_pass.local_experiments,'time',SimpleNamespace(monotonic=lambda:next(times)))
    monkeypatch.setattr(agent_local_pass.local_experiments.agent_evidence,'preflight',lambda *a:None)
    result=agent_local_pass.local_experiments.run(net,a,'game','f',revision,seconds=5)
    assert 'time budget reached' in result
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n']==0


def test_real_compile_submission_can_match_in_the_local_pass(tmp_path,monkeypatch,built):  # noqa: F811
    from decompnet.app.engine import RomNetwork

    from .test_app_engine import DAMAGE

    net=RomNetwork(tmp_path/'decomp.db',tmp_path)
    slug=net.import_rom(built,confirmed=True,log=lambda *_:None)
    monkeypatch.setattr(net.twins,'after_match',lambda *args:None)
    a,_=net.local_agent('solver');name='func_80000808'
    net.claim_row(a,slug,name)
    monkeypatch.setattr(agent_local_pass.local_experiments,'available',lambda *args,**kwargs:{'literal':[DAMAGE]})
    agent_local_pass.configure(net,{'agent_local_enabled':True,'agent_local_trials':1,'agent_local_seconds':15})
    result=tools.call(net,a,slug,'compile_and_diff',{'function':name,'source':DAMAGE.replace('= 60;','= 61;')})
    assert 'Automatic local pass:' in result and 'MATCH! Saved by the normal judge' in result,result
    fn=net._function(name,slug)
    assert fn['status']=='matched' and fn['matched_source']==DAMAGE
    provenance=net.store.one('SELECT provenance FROM repair_attempt_provenance ORDER BY attempt_id DESC LIMIT 1')
    assert 'automatic agent local pass' in provenance['provenance'] and 'literal' in provenance['provenance']

    capture=net.store.one('SELECT status FROM repair_examples ORDER BY id DESC LIMIT 1')
    assert capture['status']=='implemented'
