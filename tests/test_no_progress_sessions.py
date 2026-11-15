"""Successful CLI exits must not restart costly analysis-only matching forever."""
import json

import pytest

from decompnet.app import agents, session_followups
from tests.test_agent_audit_followups import setup


@pytest.mark.parametrize('kind', ['claude', 'opencode', 'codex', 'api'])
def test_slow_zero_build_sessions_pause_after_three_without_losing_sources(tmp_path, monkeypatch, kind):
    net, manager, session, agent = setup(tmp_path, monkeypatch)
    session.kind = kind
    # Model calls are stubbed; mark every duration as too long for the old fast-failure gate.
    monkeypatch.setattr(agents, 'FAST_FAILURE', -1)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='retained candidate',best_score=10 WHERE name='f0'")
    runs = []
    def run(s, *args):
        runs.append(s.continuation_note)
        if len(runs) > 3:manager.stop(s.id)  # Bounded even on the original broken loop.
        return True
    monkeypatch.setattr(manager, '_run_cli', run)
    if kind == 'api':
        from decompnet import agents as connectors
        monkeypatch.setattr(connectors.get('api'), 'run', lambda m, s, actor: run(s))
    manager._run(session)
    assert len(runs) == 3
    assert session_followups.NO_PROGRESS_SESSIONS == 3
    assert 'ordinary compiler' in runs[1]
    assert session.state == 'paused' and '3 consecutive sessions' in session.why
    assert net.store.one("SELECT best_source FROM functions WHERE name='f0'")[0] == 'retained candidate'
    assert net.store.one('SELECT state FROM helpers WHERE id=?', session.id)[0] == 'paused'
    assert [r['outcome'] for r in net.store.all('SELECT outcome FROM helper_sessions')] == ['early_finish','failed','failed']
    assert net.store.one('SELECT COUNT(*) FROM attempts')[0] == 0
    assert net.store.one('SELECT COUNT(*) FROM helper_messages WHERE kind=\'handoff\'')[0] == 3


@pytest.mark.parametrize('progress', ['build', 'investigation', 'rotation', 'provider-failure'])
def test_real_progress_or_external_interrupt_breaks_no_progress_streak(tmp_path, monkeypatch, progress):
    net, manager, session, agent = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(agents, 'FAST_FAILURE', -1)
    session.continuation_attempts = 1
    runs = []
    def run(s, *args):
        runs.append(1)
        if len(runs) == 3:
            if progress == 'build':session_followups.diagnostic_completed(net, agent)
            elif progress == 'investigation':session_followups.investigation_completed(net, agent, 'game', 'report_candidate_issue')
            elif progress == 'rotation':s.rotated = True
            else:return False
        if len(runs) == 6:manager.stop(s.id)
        return True
    monkeypatch.setattr(manager, '_run_cli', run)
    manager._run(session)
    assert len(runs) == 6
    assert 'consecutive sessions' not in session.why


def test_opencode_exit_reason_is_last_step_category_and_never_provider_payload():
    from decompnet.agents.opencode import OPENCODE
    state = {}
    for reason, expected in [('tool-calls','tool-calls'), ('stop','stop'), ('length','output_limit')]:
        OPENCODE.parse({'type':'step_finish','part':{'reason':reason}}, state)
        assert OPENCODE.exit_reason(state) == expected
    OPENCODE.parse({'type':'step-finish','part':{'reason':'credential=secret request body'}}, state)
    assert OPENCODE.exit_reason(state) == 'not reported'
    assert 'secret' not in json.dumps(state)


def test_cli_exit_diagnostic_is_saved_without_untrusted_finish_payload(tmp_path, monkeypatch):
    import os
    import sys
    net, manager, session, agent = setup(tmp_path, monkeypatch)
    session.kind = 'opencode'
    session.options = {'task':'readable'}
    _, token = net.local_agent(session.agent_name)
    monkeypatch.setattr(manager, '_preflight_tools', lambda *args, **kwargs: True)
    monkeypatch.setattr(manager, 'prepare_session', lambda *args, **kwargs: None)
    events = [{'type':'tool_use','name':'get_function','input':{}},
              {'type':'step_finish','part':{'reason':'credential=private', 'tokens':{}}}]
    code = 'import json; events=' + repr(events) + '; [print(json.dumps(e)) for e in events]'
    monkeypatch.setattr(manager, '_command', lambda *args: ([sys.executable,'-c',code],dict(os.environ),''))
    manager._session_begin(session)
    assert manager._run_cli(session, token, tmp_path) is True
    rows = net.store.all("SELECT text FROM helper_messages WHERE kind='termination'")
    assert len(rows) == 1
    assert rows[0]['text'] == 'CLI exited: code=0; stdout_tools_seen=true; provider_finish=not reported.'
    assert 'private' not in rows[0]['text'] and token not in rows[0]['text']
