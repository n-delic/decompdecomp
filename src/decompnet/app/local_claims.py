"""Atomic function holds shared by local checks and the replaceable permuter worker."""
import time

HOLD_SPARE_SECONDS = 120


def expiry_ids(net, store):
    """Read shared identities before acquiring a separate project's write lock."""
    return {} if store is net.store else {
        'machine_ids': net._machine_agent_ids(),
        'nonexclusive_ids': net._nonexclusive_agent_ids(),
    }


def hold(net, agent, fn, seconds, *, count_turn=True):
    now = time.time()
    store = net.project_store(fn['project_id'])
    identities = expiry_ids(net, store)
    with store.tx() as db:
        if not db.execute('SELECT 1 FROM functions WHERE id=? AND project_id=?',
                          (fn['id'], fn['project_id'])).fetchone():
            return False
        net._expire_claims(db, **identities)
        if db.execute('SELECT 1 FROM claims WHERE function_id=?', (fn['id'],)).fetchone():
            return False
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',
                   (fn['id'], agent.id, now, now+seconds+HOLD_SPARE_SECONDS))
        if count_turn:
            db.execute('UPDATE functions SET permuted=permuted+1 WHERE id=?', (fn['id'],))
    return True


def release(net, agent, fn):
    with net.project_store(fn['project_id']).tx() as db:
        db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (fn['id'], agent.id))
