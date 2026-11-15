"""Alternating catalog/project statistics must not force a commit per candidate."""
import sqlite3
from contextlib import contextmanager

import pytest

from decompnet.app import trial_bookkeeping


class Store:
    def __init__(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.execute('CREATE TABLE stats(key TEXT PRIMARY KEY,value INTEGER)')
        self.transactions=0
    @contextmanager
    def tx(self):
        self.transactions+=1
        with self.conn:yield self.conn


def write(store,key,value,**kwargs):
    trial_bookkeeping.submit(store,[('INSERT OR REPLACE INTO stats VALUES(?,?)',(key,value))],
        memo=(key,value),**kwargs)


def test_alternating_stores_batch_independently_and_reads_flush_only_their_store(monkeypatch):
    monkeypatch.setattr(trial_bookkeeping.time,'monotonic',lambda:0.)
    catalog,project=Store(),Store()
    with trial_bookkeeping.scope(maximum=True):
        for n in range(50):
            write(catalog,'policy-'+str(n),n)
            write(project,'trial-'+str(n),n)
        assert catalog.transactions==project.transactions==0
        assert trial_bookkeeping.lookup(catalog,'policy-49')==49
        assert trial_bookkeeping.lookup(project,'trial-49')==49
        trial_bookkeeping.flush(catalog)
        assert catalog.transactions==1 and project.transactions==0
        assert trial_bookkeeping.lookup(catalog,'policy-49') is None
        assert trial_bookkeeping.lookup(project,'trial-49')==49
    assert project.transactions==1
    assert catalog.conn.execute('SELECT COUNT(*) FROM stats').fetchone()[0]==50
    assert project.conn.execute('SELECT COUNT(*) FROM stats').fetchone()[0]==50


def test_immediate_winner_flushes_its_store_without_losing_other_pending_history(monkeypatch):
    monkeypatch.setattr(trial_bookkeeping.time,'monotonic',lambda:0.)
    catalog,project=Store(),Store()
    with pytest.raises(RuntimeError,match='cancelled'),trial_bookkeeping.scope():
        write(catalog,'miss',1)
        write(project,'miss',2)
        write(project,'winner',0,immediate=True)
        assert project.transactions==1 and catalog.transactions==0
        assert project.conn.execute("SELECT value FROM stats WHERE key='winner'").fetchone()[0]==0
        raise RuntimeError('cancelled')
    assert catalog.transactions==1


def test_aggregate_limit_bounds_many_stores_and_preserves_sql_order(monkeypatch):
    monkeypatch.setattr(trial_bookkeeping.time,'monotonic',lambda:0.)
    stores=[Store() for _ in range(4)]
    with trial_bookkeeping.scope():
        for n in range(64):write(stores[n%4],'same',n)
        assert all(store.transactions==1 for store in stores)
        assert not trial_bookkeeping.current().pending
    assert [store.conn.execute('SELECT value FROM stats').fetchone()[0] for store in stores]==[60,61,62,63]


def test_real_project_shard_bulk_history_preserves_feedback(tmp_path):
    from decompnet.app.engine import RomNetwork
    from tests.test_claims import _ordered_game
    from tests.test_project_database_migration import close
    net=_ordered_game(tmp_path,'rom');close(net)
    net=RomNetwork(tmp_path/'app.db',tmp_path,project_databases=True)
    try:
        project=net.project_store('game')
        fid=project.one('SELECT id FROM functions ORDER BY id LIMIT 1')['id']
        feedback='{"score":40,"note":"'+'exact retained feedback '*100+'"}'
        with trial_bookkeeping.scope(maximum=True):
            for n in range(20):
                trial_bookkeeping.submit(net.store,[('INSERT OR REPLACE INTO settings VALUES(?,?)',('history-test-'+str(n),str(n)))])
                trial_bookkeeping.submit(project,[('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                    (fid,'history-test-'+str(n),'miss','test feedback',feedback))])
        rows=project.all("SELECT feedback FROM reuse_trials WHERE signature LIKE 'history-test-%'")
        assert len(rows)==20 and all(row['feedback']==feedback for row in rows)
        assert net.store.one("SELECT value FROM settings WHERE key='history-test-19'")['value']=='19'
    finally:close(net)


def test_later_store_failure_does_not_replay_an_earlier_commit(monkeypatch):
    monkeypatch.setattr(trial_bookkeeping.time,'monotonic',lambda:0.)
    good,bad=Store(),Store()
    with trial_bookkeeping.scope():
        write(good,'first',1)
        trial_bookkeeping.submit(bad,[('INSERT INTO nonexistent VALUES(?)',(1,))])
        batch=trial_bookkeeping.current()
        with pytest.raises(sqlite3.OperationalError):batch.flush()
        assert good.transactions==1
        bad.conn.execute('CREATE TABLE nonexistent(value INTEGER)')
        batch.flush()
        assert good.transactions==1
    assert good.conn.execute('SELECT value FROM stats').fetchone()[0]==1
    assert bad.conn.execute('SELECT value FROM nonexistent').fetchone()[0]==1
