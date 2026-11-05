"""Contributor caching stays correct after corrections and never reads saved C."""
import logging
import sqlite3

from decompnet.app.history_summary import summary
from decompnet.store import Store


def populated(tmp_path):
    store = Store(tmp_path / 'history.db')
    with store.tx() as db:
        for pid in (1, 2):
            db.execute('INSERT INTO projects(id,slug,root,config) VALUES(?,?,?,?)', (pid, str(pid), str(tmp_path), '{}'))
            db.execute('INSERT INTO functions(id,project_id,name,n_insns) VALUES(?,?,?,10)', (pid, pid, 'f'))
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at,source) VALUES(1,1,800,1,10,?)',
                   ('private source' * 10000,))
    return store


def test_cached_history_invalidates_for_corrections_deletes_and_moves(tmp_path):
    store = populated(tmp_path)
    first = summary(store, 1)
    assert first['agents'][1]['improved'] == {1}
    assert summary(store, 1) is first
    with store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled) VALUES(2,2,500,1)')
    assert summary(store, 1) is first  # unrelated project writes do not evict the cache
    with store.tx() as db:
        db.execute('UPDATE attempts SET score=1000 WHERE function_id=1')
    corrected = summary(store, 1)
    assert not corrected['agents'][1]['improved']
    with store.tx() as db:
        db.execute('UPDATE attempts SET score=900,score_improved=0 WHERE function_id=1')
    assert summary(store, 1)['agents'][1]['improved'] == {1}
    assert not summary(store, 1)['agents'][1]['verified_improved']
    with store.tx() as db:
        db.execute('UPDATE functions SET n_insns=8 WHERE id=1')
    assert not summary(store, 1)['agents'][1]['improved']
    with store.tx() as db:
        db.execute("UPDATE functions SET project_id=2,name='moved' WHERE id=1")
    assert not summary(store, 1)['agents']
    assert summary(store, 2)['agents'][1]['worked'] == {1}
    with store.tx() as db:
        db.execute('DELETE FROM attempts WHERE function_id=1')
    assert 1 not in summary(store, 2)['agents']


def test_cached_read_does_not_scan_attempts_and_cold_read_is_covering(tmp_path):
    store = populated(tmp_path)
    reader = store._reader()
    def metadata_only(action, table, column, *_):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'attempts' and column == 'source' else sqlite3.SQLITE_OK
    reader.set_authorizer(metadata_only)
    queries = []
    reader.set_trace_callback(queries.append)
    first = summary(store, 1)
    query = next(q for q in queries if q.startswith('SELECT t.id'))
    assert any('COVERING INDEX idx_attempts_history' in r['detail'] for r in store.all('EXPLAIN QUERY PLAN ' + query))
    queries.clear()
    assert summary(store, 1) is first
    assert all('attempts' not in q for q in queries)
    reader.set_authorizer(None)
    reader.set_trace_callback(None)


def test_slow_read_diagnostics_are_rate_limited_and_do_not_log_values(tmp_path, monkeypatch, caplog):
    from decompnet import store as module
    store = populated(tmp_path)
    stamps = iter([0, .5, 1, 1.5])
    from types import SimpleNamespace
    # Do not replace the process-wide clock used by background test workers.
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: next(stamps)))
    monkeypatch.setattr(store, '_read_rows', lambda *_: [])
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        store.all('SELECT secret FROM private_table WHERE credential=?', 'sensitive value')
        store.all('SELECT secret FROM private_table WHERE credential=?', 'sensitive value')
    assert len(caplog.records) == 1
    assert 'Slow database read' in caplog.text
    assert 'sensitive' not in caplog.text and 'private_table' not in caplog.text


def test_rolled_back_history_is_never_published(tmp_path):
    store = populated(tmp_path)
    committed = summary(store, 1)
    try:
        with store.tx() as db:
            db.execute('UPDATE attempts SET score=1000 WHERE function_id=1')
            assert not summary(store, 1)['agents'][1]['improved']
            raise ValueError('rollback')
    except ValueError:
        pass
    assert summary(store, 1) is committed
    with store.tx() as db:
        db.execute('UPDATE attempts SET score=700 WHERE function_id=1')
    assert summary(store, 1)['agents'][1]['improved'] == {1}


def test_append_updates_only_new_attempts_without_mutating_old_snapshot(tmp_path):
    store = populated(tmp_path)
    first = summary(store, 1)
    with store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,2,700,1,20)')
    queries = []
    store._reader().set_trace_callback(queries.append)
    updated = summary(store, 1)
    store._reader().set_trace_callback(None)
    assert any('AND t.id>1' in q for q in queries)
    assert 2 not in first['agents']
    assert updated['agents'][2]['improved'] == {1}
    assert updated['recency'][1]['last_improved'] == 20
    with store.tx() as db:
        db.execute('INSERT INTO attempts(id,function_id,agent_id,score,compiled) VALUES(0,1,3,600,1)')
    rebuilt = summary(store, 1)
    assert rebuilt['agents'][3]['improved'] == {1}
    assert not rebuilt['agents'][2]['improved']


def test_map_recency_matches_full_history_without_building_contributors(tmp_path):
    from decompnet.app.history_summary import recency
    store = populated(tmp_path)
    with store.tx() as db:
        db.executemany('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,1,?,?,?)',
                       [(900,1,11),(None,0,12),(700,1,13),(-1,1,14),(0,1,15),(None,0,None)])
    assert recency(store,1) == summary(store,1)['recency']
    first=recency(store,1)
    assert recency(store,1,[1]) == first
    assert recency(store,1,[]) == {}
    assert recency(store,1) is first
    with store.tx() as db:
        db.execute('DELETE FROM attempts WHERE score<0')
    assert recency(store,1) == summary(store,1)['recency']
    with store.tx() as db:
        db.execute('UPDATE functions SET n_insns=1 WHERE id=1')
    assert recency(store,1) == summary(store,1)['recency']
    with store.tx() as db:
        db.execute("UPDATE functions SET project_id=2,name='moved' WHERE id=1")
    assert recency(store,1) == summary(store,1)['recency']
    assert recency(store,2) == summary(store,2)['recency']


def test_map_recency_never_publishes_rolled_back_dates(tmp_path):
    from decompnet.app.history_summary import recency
    store=populated(tmp_path)
    first=recency(store,1)
    try:
        with store.tx() as db:
            db.execute('UPDATE attempts SET created_at=100')
            assert recency(store,1)[1]['last_checked']==100
            raise RuntimeError('rollback')
    except RuntimeError:
        pass
    assert recency(store,1)==first
    with store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,1,100,1,200)')
    assert recency(store,1)==summary(store,1)['recency']


def test_function_map_does_not_build_contributor_history(tmp_path,monkeypatch):
    from decompnet.app import history_summary
    from decompnet.app.engine import RomNetwork
    from tests.test_loading import tiny_game
    net=RomNetwork(tmp_path/'map.db',tmp_path)
    tiny_game(net)
    def unexpected(*args):
        raise AssertionError('Opening the map rebuilt contributor history')
    monkeypatch.setattr(history_summary,'summary',unexpected)
    assert net.function_map('game')
