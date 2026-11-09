"""Ending an idle session must not scan every other agent's historical matches."""
from decompnet.store import Store

QUERY = "SELECT COUNT(*) AS n FROM events WHERE agent_id=? AND kind='match' AND created_at>=?"


def test_session_match_count_uses_bounded_history_and_inclusive_start(tmp_path):
    store = Store(tmp_path/'db')
    with store.tx() as db:
        db.executemany('INSERT INTO events(project_id,agent_id,kind,created_at) VALUES(1,?,?,?)',
                       [(2, 'match', 100+i) for i in range(20000)] +
                       [(1, 'note', 100+i) for i in range(20000)] +
                       [(1, 'match', 0) for i in range(20000)])
        db.execute("INSERT INTO events(project_id,agent_id,kind,created_at) VALUES(2,1,'match',100),(1,1,'match',101)")
    steps = 0
    def progress():
        nonlocal steps
        steps += 100
        return 0
    store.conn.set_progress_handler(progress, 100)
    try:
        # Existing accounting includes all this agent's projects and the start instant.
        assert store.conn.execute(QUERY, (1, 100)).fetchone()['n'] == 2
        assert store.conn.execute(QUERY, (3, 100)).fetchone()['n'] == 0
    finally:
        store.conn.set_progress_handler(None, 0)
    assert steps < 1000
    store.conn.close()


def test_existing_database_gets_match_count_index_without_changing_events(tmp_path):
    path = tmp_path/'db'
    store = Store(path)
    with store.tx() as db:
        db.execute("INSERT INTO events(project_id,agent_id,kind,created_at) VALUES(1,7,'match',10)")
    store.conn.execute('DROP INDEX IF EXISTS idx_events_agent_matches')
    store.conn.close()
    upgraded = Store(path)
    assert upgraded.one(QUERY, 7, 10)['n'] == 1
    assert upgraded.one('SELECT COUNT(*) n FROM events')['n'] == 1
    row = upgraded.one("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_events_agent_matches'")
    assert row is not None
    upgraded.conn.close()
