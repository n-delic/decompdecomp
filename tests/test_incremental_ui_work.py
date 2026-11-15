"""Tail-only agent counters and restart-safe derived views keep their original semantics."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.agent_metrics import results
from decompnet.app.live_sections import compact
from decompnet.app.view_snapshots import cross_project_ids
from decompnet.read_cache import ReadCache

from .test_claims import _game


def test_metrics_tail_corrections_resets_and_match_ownership(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    agent, _ = net.local_agent('worker')
    session = SimpleNamespace(agent_name='worker', options={})
    def add(score):
        with net.store.tx() as db:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,?,?,1,?)',
                       (agent.id, score, 10))
    add(100)
    assert results(net, 'game', [session])['worker']['improved'] == 1
    queries = []
    net.store._reader().set_trace_callback(queries.append)
    add(100)
    board = results(net, 'game', [session])['worker']
    assert board['compiles'] == 2 and board['improved'] == 1
    assert any('t.id>1' in q for q in queries)
    queries.clear()
    results(net, 'game', [session])
    assert not any('JOIN attempts t' in q for q in queries)
    add(0)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_agent_id=? WHERE id=1", (agent.id,))
    assert results(net, 'game', [session])['worker']['matched'] == 1
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=1")
    assert results(net, 'game', [session])['worker']['matched'] == 0
    with net.store.tx() as db:
        db.execute('DELETE FROM attempts WHERE score=0')
        db.execute('UPDATE attempts SET score=10000')
    assert results(net, 'game', [session])['worker']['improved'] == 0
    session.options = {'_metrics': {'attempt': 2}}
    add(9000)
    assert results(net, 'game', [session])['worker']['compiles'] == 1
    assert results(net, 'game', [session])['worker']['improved'] == 1
    with pytest.raises(ValueError):
        with net.store.tx() as db:
            db.execute('UPDATE attempts SET score=0')
            results(net, 'game', [session])
            raise ValueError('rollback')
    assert results(net, 'game', [session])['worker']['improved'] == 1


def test_polishing_counts_are_batched_and_respect_resets(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    a, _ = net.local_agent('a')
    b, _ = net.local_agent('b')
    with net.store.tx() as db:
        for aid in (a.id, b.id):
            db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(1,?,'readable','f',?)", (aid, 100 + aid))
    sessions = [SimpleNamespace(agent_name=n, options={}) for n in ('a', 'b')]
    queries = []
    net.store._reader().set_trace_callback(queries.append)
    board = results(net, 'game', sessions)
    assert board['a']['polished'] == board['b']['polished'] == 1
    # A polishing card times its latest polish ("Last polish"), not a score improvement.
    assert (board['a']['last_polish'], board['b']['last_polish']) == (100 + a.id, 100 + b.id)
    assert sum('COUNT(DISTINCT e.detail)' in q for q in queries) == 1
    sessions[0].options = {'_metrics': {'event': 2}}
    assert results(net, 'game', sessions)['a']['polished'] == 0
    assert results(net, 'game', sessions)['a']['last_polish'] is None


def test_peer_snapshot_survives_memory_cache_reset_and_invalidates(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    proj = net._project('game')
    cross_project_ids(net, proj)
    net.store.read_cache = ReadCache()
    original = net.store.all
    def no_targets(sql, *args):
        assert 'SELECT f.id,f.project_id,f.target' not in sql
        return original(sql, *args)
    net.store.all = no_targets
    assert not cross_project_ids(net, proj)
    net.store.all = original
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=X'01020304' WHERE id=1")
    cross_project_ids(net, proj)
    assert net.store.one('SELECT COUNT(*) AS n FROM read_snapshots')['n'] == 1


def test_rom_fingerprints_reuse_persistent_metadata_and_rebuild_on_relocation(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    proj = net._project('game')
    first = net.twins._rom_prints(proj)
    original = net.twins._build_rom_prints
    monkeypatch.setattr(net.twins, '_build_rom_prints', lambda _: pytest.fail('recomputed unchanged bytes'))
    assert net.twins._rom_prints(proj) == first
    monkeypatch.setattr(net.twins, '_build_rom_prints', original)
    revision = net.store.read_version('fingerprints', proj['id'])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET relocs='[]' WHERE id=1")
    assert net.store.read_version('fingerprints', proj['id']) != revision
    net.twins._rom_prints(proj)
    assert net.store.one('SELECT COUNT(*) AS n FROM read_snapshots')['n'] == 1


def test_compact_live_sections_keep_events_and_only_changed_sections():
    body = {'overview': {'matched': 2}, 'claims': [], 'sessions': [], 'events': [{'id': 1}], 'attempts': []}
    first = compact(body, None)
    second = compact(body, json.dumps(first['versions']))
    assert 'overview' not in second and 'claims' not in second and 'sessions' not in second
    assert second['events'] == body['events']
    body['overview']['matched'] = 3
    changed = compact(body, json.dumps(first['versions']))
    assert changed['overview']['matched'] == 3 and 'sessions' not in changed
    assert compact(body, '[]')['overview'] == body['overview']


def test_compact_gui_reuses_sections_and_defers_hidden_map():
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('needs node')
    subprocess.run(['node', 'tests/gui_live_sections.js'], check=True, capture_output=True, text=True)


def test_autosave_reuses_project_inventory_but_still_checks_every_project(tmp_path, monkeypatch):
    from decompnet.app.saves import Autosave
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    checked = []
    monkeypatch.setattr(net.progress, 'sync', lambda slug, **kw: checked.append((slug, kw)) or False)
    calls = []
    read = net.store.all
    def measured(sql, *args):
        if sql == 'SELECT slug,root,config FROM projects':
            calls.append(sql)
        return read(sql, *args)
    monkeypatch.setattr(net.store, 'all', measured)
    auto = Autosave(net)
    auto.run_once(); auto.run_once()
    assert len(calls) == 1 and len(checked) == 2
    assert all(kw == {'force': True, 'retry': False} for _, kw in checked)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config='{}' WHERE id=1")
    auto.run_once()
    assert len(calls) == 2


def test_fingerprint_transaction_does_not_publish_rolled_back_input(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    proj = net._project('game')
    original = net.twins.prints(proj)
    with pytest.raises(ValueError):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET target=X'01020304' WHERE id=1")
            net.twins.prints(proj)
            raise ValueError('rollback')
    assert net.twins.prints(proj) == original
