import sqlite3

from decompnet import permuter_claims


def test_clear_with_agent_ids_intersects_active_claims():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE claims (function_id INTEGER, agent_id INTEGER)')
    db.executemany('INSERT INTO claims VALUES (?, ?)', [(1, 10), (2, 20), (3, 30)])

    # Large set of agent_ids where only 20 is in claims.
    permuter_claims.clear(db, agent_ids=frozenset(range(20, 5000, 100)))
    remaining = [row[0] for row in db.execute('SELECT agent_id FROM claims ORDER BY agent_id').fetchall()]
    assert remaining == [10, 30]

    # Empty agent_ids is a no-op.
    permuter_claims.clear(db, agent_ids=frozenset())
    remaining = [row[0] for row in db.execute('SELECT agent_id FROM claims ORDER BY agent_id').fetchall()]
    assert remaining == [10, 30]
