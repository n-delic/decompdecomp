"""Permutation is speculative work, not exclusive ownership of a game function."""
import re


def worker_name(name, owner):
    return name[len(owner) + 1:] if owner and name.startswith(owner + '.') else name


def nonexclusive(agent):
    name = worker_name(getattr(agent, 'name', ''), getattr(agent, 'owner', None))
    return name in ('permuter', 'room-reconcile') or re.fullmatch(r'permuter-[0-9]+(?:-lane-[0-9]+)?', name) is not None


def clear(db, *, agent_ids=None):
    """Remove legacy permutation leases without recording an agent abandonment."""
    if agent_ids is not None:
        if not agent_ids:
            return
        targets = agent_ids if isinstance(agent_ids, (set, frozenset)) else set(agent_ids)
        active = {row[0] for row in db.execute('SELECT DISTINCT agent_id FROM claims').fetchall()}
        present = active & targets
        if present:
            db.executemany('DELETE FROM claims WHERE agent_id=?', [(aid,) for aid in present])
        return
    rows = db.execute("SELECT DISTINCT a.id,a.name,a.owner FROM agents a JOIN claims c ON c.agent_id=a.id "
                      "WHERE a.name LIKE '%permuter%'").fetchall()
    from types import SimpleNamespace
    ids = [(row['id'],) for row in rows if nonexclusive(SimpleNamespace(**dict(row)))]
    db.executemany('DELETE FROM claims WHERE agent_id=?', ids)


def hold(net, agent, fn, seconds, *, count_turn=True):
    if not nonexclusive(agent):
        from .app.local_claims import hold
        return hold(net, agent, fn, seconds, count_turn=count_turn)
    store = net.project_store(fn['project_id'])
    if not count_turn:
        row = store.one(
            'SELECT f.status, c.agent_id AS legacy_claim FROM functions f '
            'LEFT JOIN claims c ON c.function_id=f.id AND c.agent_id=? WHERE f.id=?',
            agent.id, fn['id'])
        if not row or row['status'] != 'open':
            return False
        if row['legacy_claim'] is not None:
            from .app.local_claims import release
            release(net, agent, fn)
        return True
    with store.tx() as db:
        row = db.execute('SELECT status FROM functions WHERE id=?', (fn['id'],)).fetchone()
        if not row or row['status'] != 'open':
            return False
        db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (fn['id'], agent.id))
        db.execute('UPDATE functions SET permuted=permuted+1 WHERE id=?', (fn['id'],))
    return True


def release(net, agent, fn):
    from .app.local_claims import release as release_claim

    if nonexclusive(agent):
        store = net.project_store(fn['project_id'])
        if not store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?', fn['id'], agent.id):
            return
    release_claim(net, agent, fn)
