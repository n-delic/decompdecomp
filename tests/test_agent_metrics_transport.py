"""Agent cards retain score history while batching native SQLite row transport."""
import sqlite3
from types import SimpleNamespace

import pytest

from decompnet.app import agent_metrics
from tests.test_project_storage import scoped  # noqa: F401


@pytest.fixture
def card(scoped):  # noqa: F811
    net, stores = scoped
    # The sharded fixture uses base Network; cards normally receive the engine facade.
    net.project_row = net._project
    return net, stores


def session(after=0):
    return SimpleNamespace(agent_name='worker', options={'_metrics': {'attempt': after}})


def test_history_and_match_ownership_cross_sqlite_in_batches(card, monkeypatch):
    net, stores = card
    store = stores[1]
    with store.tx() as db:
        db.execute('UPDATE functions SET n_insns=4 WHERE id=10')
        # Earlier work by a different helper remains the baseline after a reset.
        db.execute("INSERT INTO agents(id,name,token_hash) VALUES(2,'old helper','other')")
        db.execute('INSERT INTO attempts(id,function_id,agent_id,score,compiled,created_at) VALUES(1,10,2,50,1,1)')
        db.executemany('INSERT INTO attempts(id,function_id,agent_id,score,compiled,created_at) VALUES(?,10,1,50,1,?)',
                       [(i, i) for i in range(2, 303)])
        db.executemany('INSERT INTO attempts(id,function_id,agent_id,score,compiled,created_at) VALUES(?,10,1,?,?,?)',
                       [(303,None,0,None),(304,75,1,304),(305,0,1,305)])
        db.execute("UPDATE functions SET status='matched',matched_agent_id=1 WHERE id=10")
    with stores[2].tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(20,1,0,1,999)')
    reads = []
    original = store.all
    def counted(sql, *args):
        rows = original(sql, *args)
        reads.append((sql, len(rows)))
        return rows
    monkeypatch.setattr(store, 'all', counted)
    def no_source(action, table, column, *_):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'attempts' and column == 'source' else sqlite3.SQLITE_OK
    reader = store._reader()
    reader.set_authorizer(no_source)
    try:
        result = agent_metrics.results(net, 'first', [session(1)])['worker']
    finally:
        reader.set_authorizer(None)
    assert result == {'agent':'worker', 'compiles':304, 'worked':1, 'improved':1,
                      'matched':1, 'last_improvement':305, 'polished':0}
    # The large ordered history and current ownership each return one aggregate,
    # never one native row per attempt/function. Other small label/event reads remain.
    assert not any('FROM main.functions' in sql for sql, _ in reads)


def test_incremental_tail_corrections_deletions_and_rollback(card):
    net, stores = card
    store = stores[1]
    with store.tx() as db:
        db.execute('UPDATE functions SET n_insns=4 WHERE id=10')
        db.execute('INSERT INTO attempts(id,function_id,agent_id,score,compiled,created_at) VALUES(1,10,1,50,1,1)')
    def result():
        return agent_metrics.results(net, 'first', [session()])['worker']
    assert result()['last_improvement'] == 1
    with store.tx() as db:
        db.execute('INSERT INTO attempts(id,function_id,agent_id,score,compiled,created_at) VALUES(2,10,1,25,1,2)')
    assert result()['compiles'] == 2 and result()['last_improvement'] == 2
    with pytest.raises(ValueError, match='rollback'):
        with store.tx() as db:
            db.execute('UPDATE attempts SET score=0,created_at=3 WHERE id=2')
            db.execute("UPDATE functions SET status='matched',matched_agent_id=1 WHERE id=10")
            assert result()['matched'] == 1 and result()['last_improvement'] == 3
            raise ValueError('rollback')
    assert result()['matched'] == 0 and result()['last_improvement'] == 2
    with store.tx() as db:
        db.execute('UPDATE attempts SET score=60 WHERE id=2')
    assert result()['last_improvement'] == 1
    with store.tx() as db:
        db.execute('DELETE FROM attempts WHERE id=1')
    assert result()['compiles'] == 1 and result()['last_improvement'] == 2
    with store.tx() as db:
        db.execute('DELETE FROM attempts WHERE id=2')
    assert result()['compiles'] == 0 and result()['last_improvement'] is None


def test_match_ownership_refreshes_without_new_attempt(card):
    net, stores = card
    with stores[1].tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(10,1,0,1,1)')
        db.execute("UPDATE functions SET status='matched',matched_agent_id=1 WHERE id=10")
    assert agent_metrics.results(net, 'first', [session()])['worker']['matched'] == 1
    with stores[1].tx() as db:
        db.execute('UPDATE functions SET matched_agent_id=NULL WHERE id=10')
    assert agent_metrics.results(net, 'first', [session()])['worker']['matched'] == 0


def test_last_improvement_preserves_timestamp_precision(card):
    net, stores = card
    stamp = 1794217763.152982
    with stores[1].tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(10,1,0,1,?)', (stamp,))
    assert agent_metrics.results(net, 'first', [session()])['worker']['last_improvement'] == stamp
