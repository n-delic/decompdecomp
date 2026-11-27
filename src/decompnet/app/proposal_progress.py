"""Resume bounded boundary proposals after measured trials, scoped to exact build/source/pool.

The compiler still judges every source. A cursor is scheduling advice, never a cached verdict.
"""
import hashlib
import time

from decomprepair import debug_boundary_repairs

from . import experiment_evidence, trial_bookkeeping

SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_proposal_progress(
 function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
 scope TEXT NOT NULL, last_source TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(function_id,scope))'''
KEEP_SCOPES = 16
_KEEP = """INSERT INTO repair_proposal_progress VALUES(?,?,?,?)
    ON CONFLICT(function_id,scope) DO UPDATE SET last_source=excluded.last_source, updated_at=excluded.updated_at
    WHERE excluded.updated_at >= repair_proposal_progress.updated_at"""
_PRUNE = """DELETE FROM repair_proposal_progress WHERE function_id=? AND scope NOT IN
    (SELECT scope FROM repair_proposal_progress WHERE function_id=? ORDER BY updated_at DESC LIMIT ?)"""


def _latest(old, new):
    return new if new[-1] >= old[-1] else old


def prepare(net, project=None):
    store = net.project_store(project) if project is not None else net.store
    ready = net.__dict__.setdefault('_proposal_progress_stores',set())
    if store in ready:return store
    with store.tx() as db:
        if store in ready:return store
        db.execute(SCHEMA)
        store.after_commit(lambda: ready.add(store))
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
        args = (self.function_id, self.scope, digest, time.time())
        prune = (self.function_id, self.function_id, KEEP_SCOPES)
        if trial_bookkeeping.current_turn() is not None:
            trial_bookkeeping.count(self.store, _KEEP, args, 2, _latest)
            trial_bookkeeping.cleanup_final(self.store, _PRUNE, prune)
        else:
            with self.store.tx() as db:
                db.execute(_KEEP, args)
                db.execute(_PRUNE, prune)


def order(net, proj, fn, source, tagged):
    """Rotate only boundary slots; generation/cancellation never advances the saved cursor."""
    tagged = list(dict.fromkeys(tagged))
    pool = [item for item in tagged if item[0] == debug_boundary_repairs.FAMILY]
    if not pool:
        return tagged, None
    store = prepare(net,fn['project_id'])
    scope = hashlib.sha256((experiment_evidence.build_scope(net,proj,fn)+source+
                           repr([hashlib.sha256(s.encode()).hexdigest() for _,s in pool])).encode()).hexdigest()
    if trial_bookkeeping.current_turn() is not None:
        saved = trial_bookkeeping.stored(store, ('proposal-cursors', fn['id']), lambda: {
            row['scope']: row['last_source'] for row in store.all(
                'SELECT scope,last_source FROM repair_proposal_progress WHERE function_id=?', fn['id'])})
        pending = trial_bookkeeping.pending_counters(store, _KEEP).get((fn['id'], scope))
        last = pending[2] if pending is not None else saved.get(scope)
    else:
        row = store.one('SELECT last_source FROM repair_proposal_progress WHERE function_id=? AND scope=?', fn['id'], scope)
        last = row['last_source'] if row else None
    hashes = [hashlib.sha256(s.encode()).hexdigest() for _,s in pool]
    start = hashes.index(last) + 1 if last in hashes else 0
    rotated = iter(pool[start:]+pool[:start])
    return [next(rotated) if f==debug_boundary_repairs.FAMILY else (f,s) for f,s in tagged], Cursor(net,fn,scope,pool)
