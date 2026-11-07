"""Resume bounded boundary proposals after measured trials, scoped to exact build/source/pool.

The compiler still judges every source. A cursor is scheduling advice, never a cached verdict.
"""
import hashlib
import threading
import time

from . import debug_boundary_repairs, experiment_evidence

SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_proposal_progress(
 function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
 scope TEXT NOT NULL, last_source TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(function_id,scope))'''
KEEP_SCOPES = 16


def prepare(net, project=None):
    store = net.project_store(project) if project is not None else net.store
    lock = net.__dict__.setdefault('_proposal_progress_lock',threading.RLock())
    with lock:
        ready = net.__dict__.setdefault('_proposal_progress_stores',set())
        if store not in ready:
            with store.tx() as db:db.execute(SCHEMA)
            ready.add(store)
    return store


class Cursor:
    def __init__(self, net, fn, scope, pool):
        self.net, self.function_id, self.scope = net, fn['id'], scope
        self.store = prepare(net,fn['project_id'])
        self.hashes = {hashlib.sha256(source.encode()).hexdigest() for _,source in pool}

    def measured(self, family, source, score):
        if family != debug_boundary_repairs.FAMILY or score is None:
            return
        digest = hashlib.sha256(source.encode()).hexdigest()
        if digest not in self.hashes:
            return
        with self.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_proposal_progress VALUES(?,?,?,?)',
                       (self.function_id,self.scope,digest,time.time()))
            db.execute('DELETE FROM repair_proposal_progress WHERE function_id=? AND scope NOT IN '
                       '(SELECT scope FROM repair_proposal_progress WHERE function_id=? ORDER BY updated_at DESC LIMIT ?)',
                       (self.function_id,self.function_id,KEEP_SCOPES))


def order(net, proj, fn, source, tagged):
    """Rotate only boundary slots; generation/cancellation never advances the saved cursor."""
    tagged = list(dict.fromkeys(tagged))
    pool = [item for item in tagged if item[0] == debug_boundary_repairs.FAMILY]
    if not pool:
        return tagged, None
    store = prepare(net,fn['project_id'])
    scope = hashlib.sha256((experiment_evidence.build_scope(net,proj,fn)+source+
                           repr([hashlib.sha256(s.encode()).hexdigest() for _,s in pool])).encode()).hexdigest()
    row = store.one('SELECT last_source FROM repair_proposal_progress WHERE function_id=? AND scope=?',fn['id'],scope)
    hashes = [hashlib.sha256(s.encode()).hexdigest() for _,s in pool]
    start = hashes.index(row['last_source'])+1 if row and row['last_source'] in hashes else 0
    rotated = iter(pool[start:]+pool[:start])
    return [next(rotated) if f==debug_boundary_repairs.FAMILY else (f,s) for f,s in tagged], Cursor(net,fn,scope,pool)
