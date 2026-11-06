"""Bounded, per-engine view snapshots; no source code or game bytes are retained here."""
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future


class MapPayload(dict):
    """A cached immutable-by-convention map has a unique wire-cache identity."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wire_key = object()


class Snapshots:
    def __init__(self, limit=100_000):
        self.limit = limit
        self.lock = threading.Lock()
        self.ready = OrderedDict()
        self.pending = {}
        self.maps = {}
        self.map_locks = {}

    def get(self, key, build, weight=lambda value: 1, ttl=30):
        with self.lock:
            item = self.ready.get(key)
            if item and item[0] > time.monotonic():
                self.ready.move_to_end(key)
                return item[1]
            future = self.pending.get(key)
            owner = future is None
            if owner:
                future = self.pending[key] = Future()
        if not owner:
            return future.result()
        try:
            value = build()
            cost = max(1, weight(value))
            with self.lock:
                if cost <= self.limit:
                    self.ready[key] = (time.monotonic() + ttl, value, cost)
                    while sum(v[2] for v in self.ready.values()) > self.limit or len(self.ready) > 64:
                        self.ready.popitem(last=False)
            future.set_result(value)
            return value
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)


def cache(net):
    # Atomic setdefault keeps one cache when the first requests arrive together.
    existing = net.__dict__.get('_view_snapshots')
    return existing if existing is not None else net.__dict__.setdefault('_view_snapshots', Snapshots())


def cross_project_ids(net, proj):
    revision = net.store.read_version('targets')
    def build():
        rows = net.store.all('SELECT f.id,f.project_id,f.target FROM functions f JOIN projects p ON p.id=f.project_id '
                             'WHERE p.arch=? AND p.big_endian=? AND f.gone_mark IS NULL AND length(f.target)>0',
                             proj['arch'], proj['big_endian'])
        owners = {}
        for row in rows:
            owners.setdefault(row['target'], set()).add(row['project_id'])
        return frozenset(row['id'] for row in rows if len(owners[row['target']]) > 1)
    def persisted():
        return frozenset(persistent(net.store, ('map-peers', proj['arch'], proj['big_endian']), revision,
                                    lambda: list(build())))
    return net.store.read_cache.get(('map-peers', proj['arch'], proj['big_endian']), revision, persisted)


def map_payload(net, slug, since=None, delta=False):
    sig = net.map_signature(slug)
    if since == sig:
        return {'sig':sig, 'same':True}
    snapshots = cache(net)
    # A direct SQL/transactional caller must never publish a snapshot that could roll back.
    transactional = getattr(net.store._local, 'in_tx', False)
    if transactional:
        rows = net.function_map(slug)
    else:
        freshness = net.store.one("SELECT COALESCE(h.revision,0),COALESCE(h.rewritten,0),COALESCE(t.attempts,0) "
                                  "FROM projects p LEFT JOIN history_revisions h ON h.project_id=p.id "
                                  "LEFT JOIN project_totals t ON t.project_id=p.id WHERE p.slug=?",slug)
        claims = net.store.all("SELECT c.function_id,c.agent_id,c.expires_at FROM claims c JOIN functions f ON f.id=c.function_id "
                                "JOIN projects p ON p.id=f.project_id WHERE p.slug=? ORDER BY c.function_id",slug)
        link = net.links.get(slug)
        from .function_searches import active
        fingerprint = (tuple(freshness),tuple(tuple(c) for c in claims),repr(link.claims) if link else '',active(net,slug))
        rows = snapshots.get(('map',slug,sig,fingerprint),lambda:incremental_map(net, slug),weight=len)
    keys = list(rows[0]) if rows else []
    def arrays():
        return MapPayload(sig=sig,keys=keys,rows=[[r[k] for k in keys] for r in rows])
    full = arrays() if transactional else snapshots.get(('map-arrays',slug,sig,fingerprint), arrays, weight=lambda value:len(rows))
    if delta and since:
        with snapshots.lock:
            previous = next((value for key,value in reversed(snapshots.ready.items()) if key[:3] == ('map',slug,since)),None)
        if previous:
            old = previous[1]
            # Structural changes need a full layout; ordinary status/score changes replace only their rows.
            if len(old) == len(rows) and all(a['name']==b['name'] and a['vram']==b['vram'] and a['unit']==b['unit']
                                             and a['size']==b['size'] for a,b in zip(old,rows)):
                changed = [[i, full['rows'][i]] for i,(a,b) in enumerate(zip(old,rows)) if a != b]
                if len(changed) < len(rows) // 2:
                    return {'sig':sig,'base':since,'keys':keys,'delta':changed}
    return full


def persistent(store, key, version, build):
    """Private, revision-checked derived metadata survives a restart; uncommitted reads never publish."""
    if getattr(store._local, 'in_tx', False):
        return build()
    key, version = json.dumps(key), json.dumps(version)
    row = store.one('SELECT value FROM read_snapshots WHERE key=? AND version=?', key, version)
    if row:
        try:
            return json.loads(row['value'])
        except ValueError:
            pass
    value = build()
    with store.tx() as db:
        db.execute('INSERT INTO read_snapshots VALUES(?,?,?) ON CONFLICT(key) DO UPDATE '
                   'SET version=excluded.version,value=excluded.value', (key, version, json.dumps(value)))
    return value


def incremental_map(net, slug):
    """Patch changed function rows, retaining full rebuilds for layout or shared evidence changes."""
    from .function_searches import active
    snapshots = cache(net)
    proj = net._project(slug)
    pid = proj['id']
    with snapshots.lock:
        lock = snapshots.map_locks.setdefault(pid, threading.Lock())
    with lock:
        revision = net.store.one('SELECT revision FROM map_clock WHERE id=1')['revision']
        shared = (net.store.read_version('targets'), net.store.read_version('fingerprints', pid),
                  net.store.read_version('naming', pid), net.store.read_version('sdk_names'), proj['config'],
                  repr(net.links[slug].claims) if slug in net.links else '',
                  active(net,slug))
        old = snapshots.maps.get(pid)
        rows = None
        if old and old[0] == shared:
            ids = [r['function_id'] for r in net.store.all(
                'SELECT function_id FROM map_changes WHERE project_id=? AND revision>?', pid, old[1])]
            positions = {r['id']: i for i, r in enumerate(old[2]) if 'id' in r}
            if len(positions) == len(old[2]) and len(ids) < max(1, len(old[2]) // 2):
                changed = net.function_map(slug, function_ids=ids) if ids else []
                structural = ('name', 'vram', 'size', 'unit')
                if len(changed) == len(ids) and all(r['id'] in positions and all(
                    r[k] == old[2][positions[r['id']]][k] for k in structural) for r in changed):
                    rows = list(old[2])
                    for row in changed:
                        rows[positions[row['id']]] = row
        if rows is None:
            rows = net.function_map(slug)
        with snapshots.lock:
            snapshots.maps[pid] = (shared, revision, rows)
            while len(snapshots.maps) > 16:
                snapshots.maps.pop(next(iter(snapshots.maps)))
        return rows
