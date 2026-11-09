"""Explicit truncated responses get one correction; partial JSON is never replayed."""
import json
import os
import sys

import pytest

from decompnet.agents.codex import CODEX
from decompnet.app import agents, session_followups
from tests.test_agents import URL, _game, _no_agent_settings

LIMIT='Incomplete response returned, reason: max_output_tokens'


def test_connector_retains_explicit_limit_but_not_parse_error_or_quoted_prose():
    state={}
    CODEX.parse({'type':'item.completed','item':{'type':'error','message':LIMIT}},state)
    for i in range(30):CODEX.parse({'type':'item.completed','item':{'type':'agent_message','text':f'update {i}'}},state)
    assert CODEX.exit_reason(state)=='output_limit'
    CODEX.parse({'type':'turn.completed','usage':{}},state)
    assert CODEX.exit_reason(state)=='not reported'
    for event in [{'type':'error','message':'failed to parse function arguments: EOF while parsing a string'},
                  {'type':'item.completed','item':{'type':'agent_message','text':LIMIT}},
                  {'type':'error','message':'usage limit reached'}]:
        other={};CODEX.parse(event,other)
        assert CODEX.exit_reason(other)=='not reported'


def cli(tmp_path,monkeypatch,event,code=1,recovered=False):
    _no_agent_settings(monkeypatch)
    net=_game(tmp_path);manager=agents.Manager(net,tmp_path,URL)
    monkeypatch.setattr(manager,'_preflight_tools',lambda *a,**k:True)
    monkeypatch.setattr(manager,'prepare_session',lambda *a,**k:None)
    session=manager._record('game','codex','codex-test','codex-test',{})
    agent,token=net.local_agent(session.agent_name)
    claim=net.claim_row(agent,'game')
    def command(s,*args):
        net.__dict__.setdefault('_pending_tool_handshakes',set()).discard((agent.id,s.slug))
        events=[event]+[{'type':'item.completed','item':{'type':'agent_message','text':'synthetic diagnostic'}} for _ in range(24)]
        if recovered:events.append({'type':'turn.completed','usage':{}})
        script='import sys,json\nsys.stdin.read()\n'+''.join('print('+repr(json.dumps(x))+')\n' for x in events)+'raise SystemExit('+str(code)+')'
        return [sys.executable,'-c',script],dict(os.environ),'synthetic prompt'
    monkeypatch.setattr(manager,'_command',command)
    return net,manager,session,agent,token,claim


@pytest.mark.parametrize('exit_code',[0,1])
def test_real_child_limit_failure_keeps_work_and_bounds_recovery(tmp_path,monkeypatch,exit_code):
    event={'type':'error','message':LIMIT}
    net,manager,s,agent,token,claim=cli(tmp_path,monkeypatch,event,exit_code)
    source='int synthetic_saved_candidate(void){return 7;}'
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=? WHERE id=?',(source,claim['id']))
    before=dict(net.store.one('SELECT * FROM functions WHERE id=?',claim['id']))
    s.options.update(resume_conversation='old-normal',focus_conversation='old-focus',resume_functions=[claim['name']])
    records=[];s.record=lambda kind,text:records.append((kind,text))
    assert manager._run_cli(s,token,tmp_path) is False
    assert s.output_limit_retries==1 and s.continuation_note==session_followups.OUTPUT_RECOVERY
    assert 'resume_conversation' not in s.options and 'focus_conversation' not in s.options
    assert s.options['resume_functions']==[claim['name']]
    assert 'get_candidate' in s.continuation_note and 'compile_patch' in s.continuation_note
    assert not s.limited and not s.auth_failed and not s.throttled
    assert any(kind=='termination' and 'provider_finish=output_limit' in text for kind,text in records)
    assert dict(net.store.one('SELECT * FROM functions WHERE id=?',claim['id']))==before
    assert net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',claim['id'],agent.id)
    with pytest.raises(agents.Fatal,match='after one recovery attempt'):
        manager._run_cli(s,token,tmp_path)
    assert s.output_limit_retries==1
    assert dict(net.store.one('SELECT * FROM functions WHERE id=?',claim['id']))==before


def test_unclassified_parse_failure_keeps_ordinary_error_path(tmp_path,monkeypatch):
    event={'type':'error','message':'failed to parse function arguments: EOF while parsing a string'}
    _,manager,s,_,token,_=cli(tmp_path,monkeypatch,event)
    assert manager._run_cli(s,token,tmp_path) is False
    assert s.output_limit_retries==0 and not s.continuation_note


def test_explicit_start_resets_output_recovery_budget(tmp_path,monkeypatch):
    from types import SimpleNamespace
    _,manager,s,_,_,_=cli(tmp_path,monkeypatch,{'type':'error','message':LIMIT})
    s.output_limit_retries=1;s.continuation_note='old recovery'
    monkeypatch.setattr(agents.threading,'Thread',lambda **kwargs:SimpleNamespace(start=lambda:None))
    manager._launch(s)
    assert s.output_limit_retries==0 and not s.continuation_note


def test_stop_during_partial_response_does_not_recover(tmp_path,monkeypatch):
    _,manager,s,_,token,_=cli(tmp_path,monkeypatch,{'type':'error','message':LIMIT})
    original=CODEX.parse_line
    def stop(line,state):
        parsed=original(line,state)
        s.stop.set()
        return parsed
    monkeypatch.setattr(CODEX,'parse_line',stop)
    assert manager._run_cli(s,token,tmp_path) is True
    assert s.output_limit_retries==0 and not s.continuation_note


def test_cli_recovered_turn_does_not_spend_app_retry(tmp_path,monkeypatch):
    _,manager,s,_,token,_=cli(tmp_path,monkeypatch,{'type':'error','message':LIMIT},0,recovered=True)
    assert manager._run_cli(s,token,tmp_path) is True
    assert s.output_limit_retries==0 and not s.continuation_note
