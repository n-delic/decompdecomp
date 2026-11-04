"""Historical partial improvements remain credited after another worker finishes."""
import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def test_distinct_improvements_ignore_ties_failures_and_other_projects(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    first, _ = net.local_agent('first')
    second, _ = net.local_agent('second')
    with net.store.tx() as db:
        for slug in ('demo', 'other'):
            pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                             (slug, slug, 'mips', str(tmp_path), json.dumps({'kind': 'rom'}))).lastrowid
            for name in ('one', 'two'):
                fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns) VALUES(?,?,?,?)',
                                 (pid, name, 40, 10)).lastrowid
                for agent, score, compiled in [(first.id, None, 0), (first.id, 1000, 1),
                                               (first.id, 800, 1), (first.id, 700, 1),
                                               (second.id, 700, 1), (second.id, 900, 1),
                                               (second.id, 600, 0)]:
                    db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(?,?,?,?,?)',
                               (fid, agent, score, compiled, 1))
                if name == 'one':
                    db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(?,?,?,?,?)',
                               (fid, second.id, 0, 1, 1))
                    db.execute("UPDATE functions SET status='matched',matched_agent_id=? WHERE id=?", (second.id, fid))
    board = {r['agent']: r for r in net.stats('demo')['agents']}
    assert board['first']['worked'] == 2
    assert {r['name'] for r in net.agent_results('demo','first','worked')['functions']} == {'one','two'}
    assert board['first']['improved'] == 2  # repeated improvements to each function count once
    assert board['first']['matched'] == 0
    assert board['second']['improved'] == 1  # direct match counts, ties and failures do not
    assert board['second']['matched'] == 1
    assert {r['name'] for r in net.agent_results('demo','first','improved')['functions']} == {'one','two'}
    assert net.agent_results('demo','first','matched')['functions'] == []
    assert [r['name'] for r in net.agent_results('demo','second','matched')['functions']] == ['one']
    assert [r['name'] for r in net.agent_results('demo','second','improved')['functions']] == ['one']


def test_new_workers_keep_their_counts_below_historical_leaderboard_cutoff(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                         ('demo', 'Demo', 'mips', str(tmp_path), json.dumps({'kind': 'rom'}))).lastrowid
    for i in range(30):
        agent, _ = net.local_agent(f'worker-{i}')
        with net.store.tx() as db:
            fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,status,matched_agent_id) VALUES(?,?,?,?,?,?)',
                             (pid, f'fn-{i}', 40, 10, 'matched', agent.id)).lastrowid
            for _ in range(31-i):
                db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(?,?,?,?,?)',
                           (fid, agent.id, 0, 1, 1))
    stats = net.stats('demo')
    assert len(stats['agents']) == 30
    last = stats['agents'][-1]
    assert last['agent'] == 'worker-29'
    assert last['matched'] == 1 and last['improved'] == 1 and last['compiles'] == 2
    # Further work must update the same card even while it remains below rank 24.
    agent, _ = net.local_agent('worker-29')
    with net.store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(?,?,?,?,?)',
                   (fid, agent.id, 0, 1, 2))
    updated = next(r for r in net.stats('demo')['agents'] if r['agent'] == 'worker-29')
    assert updated['compiles'] == 3 and updated['matched'] == 1 and updated['improved'] == 1


def test_agent_results_pagination_and_kind_validation(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path);agent,_=net.local_agent('worker')
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('demo','Demo','mips',?, '{}')",(str(tmp_path),)).lastrowid
        for i in range(103):
            db.execute("INSERT INTO functions(project_id,name,status,matched_agent_id) VALUES(?,?,'matched',?)",(pid,f'fn-{i}',agent.id))
    first=net.agent_results('demo','worker')
    assert len(first['functions'])==100 and first['more']
    second=net.agent_results('demo','worker',before=first['functions'][-1]['id'])
    assert len(second['functions'])==3 and not second['more']
    assert not {f['id'] for f in first['functions']} & {f['id'] for f in second['functions']}
    with pytest.raises(NetworkError):
        net.agent_results('demo','worker','invalid')


def test_function_recency_ignores_ties_failed_checks_and_worse_scores(tmp_path):
    net=RomNetwork(tmp_path/'recency.db',tmp_path)
    agent,_=net.local_agent('worker')
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('demo','Demo','mips',?, '{}')",(str(tmp_path),))
        for name in ('checked','never'):
            db.execute('INSERT INTO functions(project_id,name,n_insns,size) VALUES(1,?,10,40)',(name,))
        for score, compiled, stamp in [(1000,1,10),(800,1,20),(800,1,30),(900,1,40),(0,0,50)]:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,?,?,?,?)',(agent.id,score,compiled,stamp))
    rows={r['name']:r for r in net.function_map('demo')}
    assert rows['checked']['last_checked']==50 and rows['checked']['last_improved']==20
    assert rows['never']['last_checked'] is None and rows['never']['last_improved'] is None


def test_function_map_recency_reads_covering_index_without_saved_source(tmp_path):
    import sqlite3

    net = RomNetwork(tmp_path/'map.db', tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('game','Game','mips',?,'{}')",
                   (str(tmp_path),))
        db.execute("INSERT INTO functions(project_id,name,n_insns,size) VALUES(1,'f',10,40)")
        for score, stamp in [(900, 10), (700, 20), (700, 30), (800, 40)]:
            db.execute('INSERT INTO attempts(function_id,score,compiled,created_at,source) VALUES(1,?,1,?,?)',
                       (score, stamp, '/* large historical C */'*10000))
    queries = []
    reader = net.store._reader()
    reader.set_trace_callback(queries.append)

    def metadata_only(action, table, column, *_):
        if action == sqlite3.SQLITE_READ and table == 'attempts' and column == 'source':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    reader.set_authorizer(metadata_only)
    try:
        row = net.function_map('game')[0]
        assert row['last_checked'] == 40 and row['last_improved'] == 20
        history = next(query for query in queries if query.startswith('SELECT t.id,t.function_id,t.agent_id'))
        plan = net.store.all('EXPLAIN QUERY PLAN '+history)
        assert any('COVERING INDEX idx_attempts_history' in r['detail'] for r in plan)
    finally:
        reader.set_authorizer(None)
        reader.set_trace_callback(None)
