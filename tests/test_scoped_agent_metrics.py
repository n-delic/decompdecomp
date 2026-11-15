"""Live agent cards never inspect other projects or saved C to count work."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from decompnet.app import agent_metrics, agents

from .test_agents import URL, _game, quiet  # noqa: F401


def test_project_snapshot_filters_before_queries_and_invalidates_on_attempt(tmp_path, quiet, monkeypatch):  # noqa: F811
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    sid, = manager.start('codex', 'game')
    other, = manager.start('codex', 'game')
    manager.sessions[other].slug = 'not-even-open'
    agent, _ = net.local_agent(manager.sessions[sid].agent_name)
    calls = []
    original = agent_metrics.results
    def measured(net, slug, sessions):
        calls.append(slug)
        return original(net, slug, sessions)
    monkeypatch.setattr(agent_metrics, 'results', measured)
    def no_source(action, table, column, *_):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'attempts' and column == 'source' else sqlite3.SQLITE_OK
    reader = net.store._reader()
    reader.set_authorizer(no_source)
    try:
        rows = manager.snapshot(project='game')
        assert [r['id'] for r in rows] == [sid]
        assert manager.snapshot(project='game')[0]['metrics']['compiles'] == 0
        assert calls == ['game']
        with net.store.tx() as db:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,?,100,1,1)', (agent.id,))
        assert manager.snapshot(project='game')[0]['metrics']['compiles'] == 1
        assert calls == ['game', 'game']
    finally:
        reader.set_authorizer(None)


def test_concurrent_snapshots_share_one_metric_refresh(tmp_path, quiet, monkeypatch):  # noqa: F811
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    manager.start('codex', 'game')
    entered, release = Event(), Event()
    calls = []
    original = agent_metrics.results
    def held(net, slug, sessions):
        calls.append(slug)
        entered.set()
        assert release.wait(5)
        return original(net, slug, sessions)
    monkeypatch.setattr(agent_metrics, 'results', held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.snapshot, project='game')
        assert entered.wait(5)
        second = pool.submit(manager.snapshot, project='game')
        release.set()
        assert first.result()[0]['metrics'] == second.result()[0]['metrics']
    assert calls == ['game']
