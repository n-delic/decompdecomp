"""Blocker reports and handoff experiments remain evidence, never automatic verdicts."""
import json

import pytest

from decompnet.app import agent_review, agent_tokens, local_experiments
from decompnet.network import NetworkError
from tests.test_agent_workflow_efficiency import game


def test_repeated_unchanged_reports_request_review_without_changing_function(tmp_path):
    net, agent, fn = game(tmp_path)
    before = dict(fn)
    for _ in range(3):
        agent_review.observe(net,fn,agent,'No compile: blocked by the same unresolved trap.',{'compiles':0})
    item = agent_review.listing(net,'game')['items'][0]
    assert item['needs_review'] and item['no_compile_releases']==3
    assert dict(net._function('f','game'))==before
    assert 'not a no-C verdict' in agent_review.brief(net,net.project_row('game'),fn)
    agent_review.acknowledge(net,'game','f')
    assert agent_review.listing(net,'game')['items']==[]


def test_retry_reason_names_changed_inputs_and_resets_release_scope(tmp_path):
    net, agent, fn = game(tmp_path)
    for _ in range(3):
        agent_review.observe(net,fn,agent,'No compile: blocked',{'compiles':0})
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(){return 2;}' WHERE id=?",(fn['id'],))
    fn=net._function('f','game')
    item=agent_review.listing(net,'game')['items'][0]
    assert 'source' in item['changed'] and not item['current'] and not item['needs_review']
    assert 'source changed' in agent_review.brief(net,net.project_row('game'),fn)
    with pytest.raises(NetworkError,match='Inputs changed'):
        agent_review.acknowledge(net,'game','f')
    agent_review.observe(net,fn,agent,'No compile: blocked',{'compiles':0})
    assert agent_review.listing(net,'game')['items'][0]['no_compile_releases']==1


def test_compiled_work_and_ordinary_no_compile_release_do_not_escalate(tmp_path):
    net, agent, fn = game(tmp_path)
    for _ in range(4):
        agent_review.observe(net,fn,agent,'Blocked by a register issue',{'compiles':2})
        agent_review.observe(net,fn,agent,'Session ended, handoff saved',{'compiles':0})
    assert agent_review.listing(net,'game')['items']==[]


def test_structured_next_experiment_validates_anchor_revision_and_build(tmp_path):
    net, agent, fn = game(tmp_path)
    net.claim_row(agent,'game','f')
    source, revision=agent_tokens.candidate(net,agent,'game','f')
    proposal={'revision':revision,'old':'return 1','new':'return 2','title':'Try a different constant'}
    before=dict(net._function('f','game'))
    agent_review.save_experiment(net,agent,'game','f',proposal)
    text=agent_tokens._function_view(net,agent,'game','f','experiment')
    patch=json.loads(text.split('\n',1)[1])
    assert patch['revision']==revision and source.replace(patch['old'],patch['new'])=='int f(){return 2;}'
    assert dict(net._function('f','game'))==before
    for bad in [dict(proposal,revision='old'),dict(proposal,old='missing'),dict(proposal,new='return 1')]:
        with pytest.raises(NetworkError):
            agent_review.save_experiment(net,agent,'game','f',bad)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(){return 3;}' WHERE id=?",(fn['id'],))
    assert 'outdated' in agent_review.experiment(net,agent,'game','f')


def test_brief_bounds_ancillary_evidence_but_preserves_complete_candidate(tmp_path,monkeypatch):
    net, agent, fn=game(tmp_path)
    net.claim_row(agent,'game','f')
    source, _=agent_tokens.candidate(net,agent,'game','f')
    monkeypatch.setattr(agent_tokens,'candidate_line_note',lambda *args:'line evidence '*400)
    monkeypatch.setattr(local_experiments,'summary',lambda *args:'failed family '*400)
    # Preflight already has the candidate; retain it and isolate packet rendering from compiler availability.
    from decompnet.app import declaration_recovery
    monkeypatch.setattr(declaration_recovery,'preflight',lambda *args:(source,None))
    text=agent_tokens.brief(net,'## CLAIMED: game/f\n```c\n'+source+'\n```','game',agent)
    assert source in text
    assert text.count('line evidence')<100 and text.count('failed family')<100
    assert 'Full failed-experiment evidence' in text


def test_historical_notes_are_reviewable_but_never_presented_as_current_scope(tmp_path):
    import time
    net, agent, fn=game(tmp_path)
    with net.store.tx() as db:
        for _ in range(3):
            db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)',
                       (fn['project_id'],fn['id'],agent.id,'No compile: same unresolved asm trap',time.time()))
    item=agent_review.listing(net,'game')['items'][0]
    assert item['historical'] and not item['current'] and item['needs_review']
    assert 'unrecorded' in item['changed'][0]
    assert not agent_review.load(net,agent_review.PREFIX+str(fn['id']))
    agent_review.acknowledge(net,'game','f')
    assert agent_review.listing(net,'game')['items']==[]


def test_api_and_mcp_expose_structured_experiment_handoffs(tmp_path):
    import anyio

    from decompnet.agents.api import api_tool_schemas
    from decompnet.app import tools
    api=next(t['function'] for t in api_tool_schemas() if t['function']['name']=='release_function')
    schema=api['parameters']['properties']['next_experiment']
    assert set(schema['required'])=={'revision','old','new'}
    net,agent,_=game(tmp_path)
    server=tools.build_mcp(net,lambda ctx:(agent,'game'))
    definitions=anyio.run(server.list_tools)
    release=next(t for t in definitions if t.name=='release_function')
    assert 'next_experiment' in release.input_schema['properties']


def test_cli_limit_publishes_account_cooldown_before_session_exit(tmp_path,monkeypatch):
    import io
    import time
    from types import SimpleNamespace

    from decompnet.app import agents, session_followups
    from tests.test_agent_audit_followups import setup
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(manager,'_preflight_tools',lambda *args:None)
    monkeypatch.setattr(manager,'prepare_session',lambda *args,**kwargs:None)
    monkeypatch.setattr(manager,'_command',lambda *args:(['stub'],{},''))
    monkeypatch.setattr(manager,'_save',lambda *args:None)
    monkeypatch.setattr(manager,'account',lambda *args:None)
    monkeypatch.setattr(agents,'_proc_start',lambda pid:None)
    proc=SimpleNamespace(pid=123456789,stdin=io.StringIO(),stdout=io.StringIO(json.dumps(
        {'type':'result','is_error':True,'result':"You've hit your 5-hour limit. Try again in 1 hour."})+'\n'),
        stderr=io.StringIO(),wait=lambda:1)
    monkeypatch.setattr(agents.subprocess,'Popen',lambda *args,**kwargs:proc)
    assert manager._run_cli(s,'test-token',tmp_path) is False
    peer=manager._record('game','claude','peer','peer',{'model':'other'})
    assert session_followups.cooldown(manager,peer)>time.time()
    assert not any('exited with code' in row['text'] for row in s.feed)


def test_release_tool_keeps_a_validated_next_experiment_for_the_next_worker(tmp_path):
    from decompnet.app import tools
    net, agent, fn=game(tmp_path)
    net.claim_row(agent,'game','f')
    source,revision=agent_tokens.candidate(net,agent,'game','f')
    result=tools.call(net,agent,'game','release_function',{'function':'f','notes':'Untried constant change; candidate retained.',
        'move_on':True,'next_experiment':{'revision':revision,'old':'return 1','new':'return 2'}})
    assert 'Next experiment saved' in result
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
    other,_=net.local_agent('next-worker')
    net.claim_row(other,'game','f')
    result=tools.call(net,other,'game','get_function',{'function':'f','section':'experiment'})
    assert 'Untested historical proposal' in result and 'return 2' in result
    assert net._function('f','game')['best_source']==source


def test_release_evidence_does_not_follow_a_concurrent_source_edit(tmp_path):
    net,agent,fn=game(tmp_path)
    old=agent_review.facts(net,net.project_row('game'),fn)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f(){return 3;}' WHERE id=?",(fn['id'],))
    agent_review.observe(net,fn,agent,'No compile: blocked',{'compiles':0},snapshot=old)
    state=agent_review.load(net,agent_review.PREFIX+str(fn['id']))
    assert state['facts']==old
    item=agent_review.listing(net,'game')['items'][0]
    assert 'source' in item['changed'] and not item['current']


def test_completed_diagnostic_builds_do_not_count_as_no_compile_blocker_releases(tmp_path):
    import time

    from decompnet.app.session_followups import diagnostic_completed
    net,agent,fn=game(tmp_path)
    progress={'compiles':0,'claimed_at':time.time()-1}
    diagnostic_completed(net,agent,'f','game')
    for _ in range(3):
        agent_review.observe(net,fn,agent,'Unresolved compiler blocker',progress)
    assert agent_review.listing(net,'game')['items']==[]
    state=agent_review.load(net,agent_review.PREFIX+str(fn['id']))
    assert all(row['compiled'] for row in state['releases'])
