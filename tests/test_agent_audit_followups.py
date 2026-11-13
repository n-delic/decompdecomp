import json
import time

import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import agent_tokens, agents, session_followups, tools
from decompnet.app.efficiency_feedback import frontload
from tests.test_agents import URL, Quick, _game, _no_agent_settings


def setup(tmp_path, monkeypatch):
    _no_agent_settings(monkeypatch)
    net = _game(tmp_path)
    manager = agents.Manager(net,tmp_path,URL)
    session = manager._record('game','claude','claude-1','claude-1',{})
    session.stop = Quick()
    agent,_ = net.local_agent(session.agent_name)
    return net,manager,session,agent


def test_claim_and_note_activity_does_not_count_as_compilation(tmp_path,monkeypatch):
    net,manager,session,agent=setup(tmp_path,monkeypatch)
    before=manager._progress(agent.id)
    net.claim_row(agent,'game')
    net.record_event('game','release','administrative',agent.name)
    assert manager._progress(agent.id)==before
    assert session_followups.premature(manager,session,agent,0)
    session.last_message='Missing compiler: cannot build until it is installed.'
    assert not session_followups.premature(manager,session,agent,0)


def test_zero_compile_continuation_is_bounded_and_recorded(tmp_path,monkeypatch):
    net,manager,session,agent=setup(tmp_path,monkeypatch)
    runs=[]
    def run(s,*args):
        runs.append(s.continuation_note)
        if len(runs)==2:
            manager.stop(s.id)
        return True
    monkeypatch.setattr(manager,'_run_cli',run)
    manager._run(session)
    assert len(runs)==2 and 'ordinary compiler' in runs[1]
    assert [r['outcome'] for r in net.store.all('SELECT outcome FROM helper_sessions')]==['early_finish','stopped']
    assert not session_followups.premature(manager,session,agent,0)


def test_account_cooldown_persists_and_is_credential_scoped(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    until=time.time()+3600
    session_followups.remember_limit(manager,s,until)
    peer=manager._record('game','claude','claude-2','claude-2',{'model':'other'})
    assert session_followups.cooldown(manager,peer)==until
    # A newly constructed manager sees the same wait after an app restart.
    restored=agents.Manager(net,tmp_path,URL)
    assert session_followups.cooldown(restored,peer)==until
    assert session_followups.stagger(peer)!=session_followups.stagger(s)
    other=manager._record('game','codex','codex-1','codex-1',{})
    assert session_followups.cooldown(manager,other) is None
    s.kind=peer.kind='api'
    s.options={'base_url':'https://provider.example/v1','api_key':'secret-one'}
    peer.options={'base_url':'https://provider.example/v1','api_key':'secret-two'}
    assert session_followups.account_key(s)!=session_followups.account_key(peer)
    peer.options={**s.options,'model':'other'}
    assert session_followups.account_key(s)==session_followups.account_key(peer)
    assert 'secret' not in session_followups.account_key(s)


def test_opencode_limits_are_isolated_by_account_and_model(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    def helper(kind,model,name):
        return manager._record('game',kind,name,name,{'model':model})
    zen=helper('opencode','opencode/beam-free','zen')
    go=helper('opencode','opencode-go/beam','go')
    zen_other=helper('opencode','opencode/other-free','zen-other')
    claude_a=helper('claude','haiku','claude-a')
    claude_b=helper('claude','opus','claude-b')

    until=time.time()+3600
    session_followups.remember_limit(manager,zen,until)
    assert session_followups.cooldown(manager,zen)==until
    assert session_followups.cooldown(manager,go) is None, 'a console-account limit must not pause the Go account'
    assert session_followups.cooldown(manager,zen_other) is None, 'one model limit must not pause another model'
    assert session_followups.configuration_key(zen)!=session_followups.configuration_key(zen_other)
    # Every other connector still has one subscription: its helpers share a cooldown.
    assert session_followups.account_key(claude_a)==session_followups.account_key(claude_b)


def test_account_wait_precedes_any_provider_session(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    session_followups.remember_limit(manager,s,time.time()+3600)
    monkeypatch.setattr(manager,'_run_cli',lambda *a:pytest.fail('No provider session during cooldown'))
    def wait(session,why,until,todo='',**kwargs):
        assert 'provider account' in why
        manager.stop(session.id)
    monkeypatch.setattr(manager,'_wait',wait)
    manager._run(s)
    assert s.runs==0 and not net.store.one('SELECT 1 FROM helper_sessions')


def test_context_inference_never_chooses_another_agents_claim(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    fn=net.claim_row(agent,'game')
    seen=[]
    monkeypatch.setattr(net,'get_context',lambda slug,function:seen.append(function) or 'header')
    assert tools._get_context(net,agent,'game')=='header'
    assert seen==[fn['name']]
    other,_=net.local_agent('other')
    tools._get_context(net,other,'game')
    assert seen[-1] is None


def test_candidate_base_is_enum_in_api_schema():
    # API schema is not a free-form string where a revision hash can be mistaken for a base.
    tool=next(t for t in api_tool_schemas() if t['function']['name']=='get_candidate')
    assert set(tool['function']['parameters']['properties']['base']['enum'])=={'current','saved_best','last_compiling','reported_corrected'}
    # get_context must actually expose the optional function argument to API agents.
    context=next(t for t in api_tool_schemas() if t['function']['name']=='get_context')
    assert 'function' in context['function']['parameters']['properties']


def test_current_revision_is_frontloaded_without_duplicate_state(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    fn=net.claim_row(agent,'game')
    source,revision=agent_tokens.candidate(net,agent,'game',fn['name'])
    text=f'score 10\nContinuation state: function={fn["name"]}; revision={revision}; status=open; claim=held; applied_patch_ids=1. Patch this revision only.'
    result=frontload(net,agent,'game',fn['name'],text)
    assert result.startswith('Continuation state:') and result.count('Continuation state:')==1
    assert 'applied_patch_ids=1' in result
    stale=text.replace(revision,'old')
    result=frontload(net,agent,'game',fn['name'],stale)
    assert revision in result.splitlines()[0] and 'revision=old' not in result


def test_unsupported_extension_is_a_review_not_a_no_c_vote(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    fn=net.claim_row(agent,'game')
    why='No C can produce it: asm("$31") register variable rejected by tooling; __builtin_return_address reloads the old value.'
    result=net.release(agent,fn['name'],why,'game',not_c=True)
    assert 'No assembly verdict recorded' in result
    current=net._function(fn['name'],'game');meta=json.loads(current['meta'])
    assert current['status']=='open' and not meta.get('not_c') and meta['assembly_review']
    assert net.not_c_from_notes('game')==0


def test_mcp_candidate_base_enum_and_context_schema(tmp_path,monkeypatch):
    import anyio
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    server=tools.build_mcp(net,lambda ctx:(agent,'game'))
    definitions=anyio.run(server.list_tools)
    schema=next(t for t in definitions if t.name=='get_candidate').input_schema
    assert set(schema['properties']['base']['enum'])=={'current','saved_best','last_compiling','reported_corrected'}


def test_one_low_effort_compile_can_receive_guidance_but_full_budget_cannot(tmp_path,monkeypatch):
    from decompnet.app.session_budget import Budget
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    s.last_message='I stopped there because the effort setting is low.'
    assert session_followups.premature(manager,s,agent,1)
    assert not session_followups.premature(manager,s,agent,2)
    s.context_budget=Budget(tools=80)
    s.context_budget.calls=60
    assert not session_followups.premature(manager,s,agent,0)
    s.context_budget=None
    s.options['task']='readable'
    assert not session_followups.premature(manager,s,agent,0)


def test_completed_diagnostic_builds_count_but_admin_events_do_not(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    before=manager._progress(agent.id)
    session_followups.diagnostic_completed(net,agent)
    assert manager._progress(agent.id)==before+1
    assert not net.store.one('SELECT 1 FROM attempts WHERE agent_id=?',agent.id)


def test_compile_response_has_one_summary_and_preserves_score_and_warnings(tmp_path):
    from decompnet import scoring
    from tests.test_claims import _game as claim_game
    net,_=claim_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    fn=net._function('f','game');rows=[('r','move a,b','move a,c')]
    net._last_diffs[agent.id,fn['id']]=(rows,scoring.LEGEND)
    original=('score 10 (90% match)\nYour last change: no effect on the score.\n'
              'The first 0 of 10 target instructions match; the first difference (regalloc): old detail\n'
              'Mismatch categories (advisory): 1 register.\n1 regalloc\nmarkers: r=regalloc\nlisting\n'
              'compiler output:\nimportant compiler warning')
    result=agent_tokens.feedback(net,agent,'game','f',original,None)
    assert result.startswith('score 10') and 'important compiler warning' in result
    assert result.count('Mismatch focus:')==1 and '1 register' in result
    assert 'Mismatch categories' not in result and '1 regalloc' not in result and 'old detail' not in result
    assert len(result)<len(original)+500
    assert 'Advisory instruction window' not in result  # Changed rows already provide the instruction view.


def test_frontload_refreshes_claim_state_even_when_revision_did_not_change(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    fn=net.claim_row(agent,'game');_,revision=agent_tokens.candidate(net,agent,'game',fn['name'])
    old=f'Continuation state: function={fn["name"]}; revision={revision}; status=open; claim=held; applied_patch_ids=1. Patch this revision only.'
    with net.store.tx() as db:db.execute('DELETE FROM claims WHERE agent_id=?',(agent.id,))
    result=frontload(net,agent,'game',fn['name'],old)
    assert 'claim=not held' in result and 'claim=held' not in result
    assert 'applied_patch_ids=1' in result and 'Do not patch without a claim' in result


def test_provider_wait_rechecks_wall_clock_after_system_sleep(tmp_path,monkeypatch):
    net,manager,s,_=setup(tmp_path,monkeypatch)
    now=[time.time()]
    monkeypatch.setattr(agents.time,'time',lambda:now[0])
    deadline=now[0]+3600
    checks=[]
    class Sleep:
        def is_set(self):return False
        def wait(self,seconds):
            checks.append(seconds)
            now[0]+=7200  # The computer wakes after the provider's wall-clock reset.
    s.stop=Sleep()
    manager._wait(s,'Provider limit reached',deadline,wall_clock=True)
    assert checks==[30]
    assert s.state=='starting' and s.until is None
    assert 'checking provider' in s.why.lower()


def test_provider_wait_honors_deadline_when_clock_moves_back_and_can_stop(tmp_path,monkeypatch):
    net,manager,s,_=setup(tmp_path,monkeypatch)
    now=[time.time()];deadline=now[0]+60
    monkeypatch.setattr(agents.time,'time',lambda:now[0])
    class Clock(Quick):
        def wait(self,seconds):
            result=super().wait(seconds)
            now[0]+= -60 if len(self.waits)==1 else seconds
            return result
    s.stop=Clock()
    manager._wait(s,'Provider limit reached',deadline,wall_clock=True)
    assert now[0]==deadline and s.stop.waits==[30]*5
    s.stop=Quick()
    def cancel(seconds):s.stop.set();return True
    s.stop.wait=cancel
    manager._wait(s,'Provider limit reached',now[0]+3600,wall_clock=True)
    assert s.stop.is_set() and s.until==now[0]+3600


def test_manual_provider_retry_tests_one_worker_then_releases_siblings_on_success(tmp_path,monkeypatch):
    net,manager,s,_=setup(tmp_path,monkeypatch)
    until=time.time()+3*86400
    s.cooldown_observed=session_followups.remember_limit(manager,s,until)
    peer=manager._record('game','claude','peer','peer',{})
    peer.cooldown_observed=until
    s.state='waiting';s.provider_waiting=True
    assert manager.retry_provider(s.id)=={'requested':True}
    assert s.provider_retry_requested and not peer.provider_retry_requested
    manager._wait(s,'Provider limit',until,wall_clock=True)
    assert s.state=='starting' and not s.provider_retry_requested
    assert session_followups.active_limit(manager,peer)  # Retry alone is not proof the quota is free.
    session_followups.clear_limit(manager,s)
    assert not session_followups.active_limit(manager,peer)
    manager._wait(peer,'Provider limit',until,wall_clock=True)
    assert peer.state=='starting' and not peer.provider_waiting
    with pytest.raises(ValueError,match='not waiting'):manager.retry_provider(peer.id)


def test_successful_retry_does_not_erase_a_newer_provider_limit(tmp_path,monkeypatch):
    net,manager,s,_=setup(tmp_path,monkeypatch)
    s.cooldown_observed=session_followups.remember_limit(manager,s,time.time()+3600)
    newer=session_followups.remember_limit(manager,s,time.time()+7200)
    session_followups.clear_limit(manager,s)
    peer=manager._record('game','claude','peer','peer',{})
    assert session_followups.cooldown(manager,peer)==newer


def test_successful_provider_session_automatically_releases_observed_cooldown(tmp_path,monkeypatch):
    net,manager,s,_=setup(tmp_path,monkeypatch)
    s.cooldown_observed=session_followups.remember_limit(manager,s,time.time()+86400)
    runs=[]
    def run(*args):
        runs.append(1)
        if len(runs)==2:
            assert not session_followups.active_limit(manager,s)
            manager.stop(s.id)
        return True
    monkeypatch.setattr(manager,'_run_cli',run)
    manager._run(s)
    assert runs==[1,1] and not session_followups.active_limit(manager,s)
