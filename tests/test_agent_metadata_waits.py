"""In-flight metadata before launch is a local wait, never an agent/model failure."""
import pytest

from decompnet.app import agents
from decompnet.read_cache import ReadPending
from tests.test_agent_audit_followups import setup


def test_repeated_selection_metadata_waits_do_not_start_or_fail_sessions(tmp_path, monkeypatch):
    net, manager, session, agent = setup(tmp_path, monkeypatch)
    reads, waits = [], []
    original = manager._wait
    def wait(s, why, *args, **kwargs):
        waits.append(why)
        return original(s, why, *args, **kwargs)
    monkeypatch.setattr(manager, '_wait', wait)
    def pending(*args):
        reads.append(1)
        if len(reads) == 7:manager.stop(session.id)
        raise ReadPending('Synthetic metadata is in flight')
    monkeypatch.setattr(net, 'selection_counts', pending)
    monkeypatch.setattr(manager, '_run_cli', lambda *args: pytest.fail('pending metadata launched model'))
    manager._run(session)
    assert len(reads) == 7
    assert session.runs == session.restarts == 0
    assert net.store.one('SELECT COUNT(*) FROM helper_sessions')[0] == 0
    assert len(waits) == 6
    assert all('project metadata before the next model request' in why for why in waits)


@pytest.mark.parametrize('stage', ['selection', 'prompt'])
def test_late_cli_preparation_pending_never_reaches_model_process(tmp_path, monkeypatch, stage):
    net, manager, session, agent = setup(tmp_path, monkeypatch)
    _, token = net.local_agent(session.agent_name)
    monkeypatch.setattr(manager, '_preflight_tools', lambda *args, **kwargs: True)
    monkeypatch.setattr(manager, 'prepare_session', lambda *args, **kwargs: None)
    def pending(*args, **kwargs):raise ReadPending('Synthetic metadata is in flight')
    if stage == 'selection':monkeypatch.setattr(net, 'selection_counts', pending)
    else:monkeypatch.setattr(manager, 'prepare_session', pending)
    monkeypatch.setattr(agents.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('metadata wait launched model'))
    with pytest.raises(agents.StartupMetadataPending):manager._run_cli(session, token, tmp_path)


def test_late_metadata_retries_have_distinct_outcome_without_fast_failure_pause(tmp_path, monkeypatch):
    net, manager, session, agent = setup(tmp_path, monkeypatch)
    calls = []
    def pending(s, *args):
        calls.append(1)
        if len(calls) == 7:manager.stop(s.id)
        raise agents.StartupMetadataPending('Synthetic local metadata wait')
    monkeypatch.setattr(manager, '_run_cli', pending)
    manager._run(session)
    assert len(calls) == 7 and session.restarts == 0
    assert [r[0] for r in net.store.all('SELECT outcome FROM helper_sessions')] == ['metadata_retry'] * 6 + ['stopped']
    assert session.state != 'paused'
    assert not net.store.one("SELECT 1 FROM helper_messages WHERE kind='error' AND text LIKE '%failed%'")
