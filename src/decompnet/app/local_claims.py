"""Atomic function holds shared by local checks and the replaceable permuter worker."""
import time

HOLD_SPARE_SECONDS = 120


def hold(net, agent, fn, seconds, *, count_turn=True):
    now = time.time()
    with net.store.tx() as db:
        net._expire_claims(db)
        if db.execute('SELECT 1 FROM claims WHERE function_id=?', (fn['id'],)).fetchone():
            return False
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',
                   (fn['id'], agent.id, now, now+seconds+HOLD_SPARE_SECONDS))
        if count_turn:
            db.execute('UPDATE functions SET permuted=permuted+1 WHERE id=?', (fn['id'],))
    return True


def release(net, agent, fn):
    with net.store.tx() as db:
        db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (fn['id'], agent.id))
