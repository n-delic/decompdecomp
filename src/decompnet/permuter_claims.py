"""Permutation is speculative work, not exclusive ownership of a game function."""
import re


def nonexclusive(agent):
    name = getattr(agent, 'name', '')
    owner = getattr(agent, 'owner', None)
    if owner and name.startswith(owner+'.'):name=name[len(owner)+1:]
    return name == 'permuter' or re.fullmatch(r'permuter-[0-9]+(?:-lane-[0-9]+)?', name) is not None


def clear(db, *, agent_ids=None):
    """Remove legacy permutation leases without recording an agent abandonment."""
    if agent_ids is not None:
        db.executemany('DELETE FROM claims WHERE agent_id=?', [(aid,) for aid in agent_ids])
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
    with net.project_store(fn['project_id']).tx() as db:
        row=db.execute('SELECT status FROM functions WHERE id=?',(fn['id'],)).fetchone()
        if not row or row['status']!='open':return False
        db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?',(fn['id'],agent.id))
        if count_turn:db.execute('UPDATE functions SET permuted=permuted+1 WHERE id=?',(fn['id'],))
    return True
