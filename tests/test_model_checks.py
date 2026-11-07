"""Model filtering must not hydrate unrelated attempts or change historical session attribution."""
import re
import sqlite3
from types import SimpleNamespace

import pytest

from decompnet import model_checks
from decompnet.network import NetworkError


class History:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.attempt_rows = []
        self.db.executescript('''
            CREATE TABLE projects(id INTEGER PRIMARY KEY,slug TEXT UNIQUE);
            CREATE TABLE functions(id INTEGER PRIMARY KEY,project_id INTEGER);
            CREATE TABLE agents(id INTEGER PRIMARY KEY,name TEXT UNIQUE);
            CREATE TABLE attempts(function_id INTEGER,agent_id INTEGER,created_at REAL);
            CREATE INDEX idx_attempts_agent_time ON attempts(agent_id,created_at);
            CREATE TABLE helpers(project TEXT,name TEXT,model TEXT);
            CREATE TABLE helper_sessions(id INTEGER PRIMARY KEY,project TEXT,agent TEXT,model TEXT,started_at REAL,ended_at REAL);
            INSERT INTO projects VALUES(1,'game'),(2,'other');
            INSERT INTO functions VALUES(1,1),(2,1),(3,1),(4,1),(5,2);
            INSERT INTO agents VALUES(1,'old-worker'),(2,'other-model'),(3,'new-worker');
            INSERT INTO helpers VALUES('game','new-worker','model-a#high');
            INSERT INTO helper_sessions VALUES
                (1,'game','old-worker','model-a#low',10,20),
                (2,'game','old-worker','model-b',21,29),
                (3,'game','old-worker','model-a',30,NULL),
                (4,'game','old-worker','model-a#high',12,18),
                (5,'other','other-model','model-a',0,NULL);
            INSERT INTO attempts VALUES(1,1,10),(2,1,20),(3,1,40),(4,1,25),(4,2,15),(5,1,15),(1,1,15);
        ''')
        self.db.executemany('INSERT INTO attempts VALUES(1,1,?)', [(0,)]*20000)

    def all(self, sql, *args):
        rows = self.db.execute(sql, args).fetchall()
        if re.search(r'\b(?:FROM|JOIN)\s+attempts\b', sql, re.I):
            self.attempt_rows.append(len(rows))
        return rows

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()


@pytest.fixture
def history():
    store = History()
    yield store
    store.db.close()


def test_model_filter_only_returns_distinct_functions_inside_own_project_sessions(history):
    seen = []
    net = SimpleNamespace(project_store=lambda project: seen.append(project) or history)
    assert model_checks.tried(net, 'game', SimpleNamespace(name='new-worker')) == {1,2,3}
    assert seen == ['game']
    assert history.attempt_rows == [3]  # no transfer of 20,000 unrelated historical attempts


def test_new_model_without_recorded_sessions_never_reads_attempts(history):
    history.db.execute("UPDATE helpers SET model='brand-new-model'")
    net = SimpleNamespace(project_store=lambda project: history)
    assert model_checks.tried(net, 'game', SimpleNamespace(name='new-worker')) == set()
    assert history.attempt_rows == []


def test_session_fallback_uses_latest_model_without_helper_record(history):
    net = SimpleNamespace(project_store=lambda project: history)
    assert model_checks.tried(net, 'game', SimpleNamespace(name='old-worker')) == {1,2,3}


def test_changed_helper_model_uses_its_own_historical_windows(history):
    history.db.execute("UPDATE helpers SET model='model-b#low'")
    net = SimpleNamespace(project_store=lambda project: history)
    assert model_checks.tried(net, 'game', SimpleNamespace(name='new-worker')) == {4}


def test_no_session_table_returns_empty_without_loading_attempts(history):
    history.db.execute('DROP TABLE helper_sessions')
    net = SimpleNamespace(project_store=lambda project: history)
    assert model_checks.tried(net, 'game', SimpleNamespace(name='new-worker')) == set()
    assert history.attempt_rows == []


def test_missing_model_identity_still_rejects_filter(history):
    net = SimpleNamespace(project_store=lambda project: history)
    with pytest.raises(NetworkError, match='explicit model'):
        model_checks.tried(net, 'game', SimpleNamespace(name='unknown'))
    assert history.attempt_rows == []
