"""Manual header-history pruning and database compaction, separate from transcript retention."""
import json
import shutil
import sqlite3
from pathlib import Path

from .. import header_storage

BATCH = 50


def _tables(store):
    return {r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}


def _references(store):
    tables = _tables(store)
    keep = {(r['project_id'], r['version']) for r in store.all(
        'SELECT project_id,MAX(version) version FROM context_history GROUP BY project_id,unit')}
    keep.update((r['project_id'], r['context_version']) for r in store.all(
        'SELECT DISTINCT f.project_id,a.context_version FROM attempts a JOIN functions f ON f.id=a.function_id'))
    # A repair can outlive its before/after attempts; preserve its captured version independently.
    unknown = set()
    if 'repair_examples' in tables:
        fields = []
        for stage in ('before', 'after', 'previous'):
            for expression, suffix in ((f"json_type(evidence,'$.{stage}')", 'type'),
                                       (f"json_extract(evidence,'$.{stage}.context_version')", 'version')):
                fields.append(f"CASE WHEN json_valid(evidence) THEN {expression} END AS {stage}_{suffix}")
        # Return only small reference metadata, never whole captured source/assembly payloads.
        for row in store.all('SELECT project_id,json_valid(evidence) valid,' + ','.join(fields) + ' FROM main.repair_examples'):
            stages = [stage for stage in ('before', 'after', 'previous') if row[stage+'_type'] not in (None, 'null')]
            if not row['valid'] or not stages:
                unknown.add(row['project_id'])
            for stage in stages:
                version = row[stage+'_version']
                if type(version) is int: keep.add((row['project_id'], version))
                else: unknown.add(row['project_id'])
    if 'verified_matches' in tables:
        for row in store.all('SELECT f.project_id,m.details FROM verified_matches m JOIN functions f ON f.id=m.function_id'):
            details = json.loads(row['details'])
            keep.add((row['project_id'], details.get('context_version')))
    # Boundary undo validates a hash of the entire historical header list.
    projects = {r['project_id'] for r in store.all('SELECT DISTINCT project_id FROM context_history')
                if store.one("SELECT 1 FROM settings WHERE key LIKE ? LIMIT 1", 'boundary-repair:'+str(r['project_id'])+':%')}
    keep.update((r['project_id'], r['version']) for r in store.all('SELECT project_id,version FROM context_history') if r['project_id'] in projects | unknown)
    with store.header_codec.lock:
        keep.update(store.header_codec.pins)
    return keep


def summary(net):
    keep = _references(net.store)
    candidates = [(r['project_id'], r['version']) for r in net.store.all('SELECT project_id,version FROM context_history')
                  if (r['project_id'], r['version']) not in keep]
    return {**header_storage.stats(net.store), 'unused_versions': len(candidates)}, candidates


def prune(net, log):
    from . import maintenance
    store = net.store
    waiting_logged=False
    def waiting():
        nonlocal waiting_logged
        log.check_cancel()
        if not waiting_logged:
            log('Another database writer is busy; waiting and retrying header cleanup. Stop cleanup cancels this wait.')
            waiting_logged=True
        log.progress({'phase':'Waiting for database writer · cleanup will retry automatically'})
    log('Checking saved header references before cleanup…')
    log.progress({'phase':'Checking saved header references'})
    evidence_revision = store.read_version("repair_profiles")
    _, candidates = summary(net)
    done = 0
    for start in range(0, len(candidates), BATCH):
        log.check_cancel()
        with store.tx(wait=waiting) as db, store.header_codec.lock:
            if store.read_version("repair_profiles") != evidence_revision:
                raise ValueError("Repair evidence changed during cleanup. Preview and run header cleanup again.")
            # New compilation reads pin their header. New attempts also protect externally
            # inserted references; never act solely on an earlier preview's candidate list.
            for project, version in candidates[start:start+BATCH]:
                if (project, version) in store.header_codec.pins: continue
                if db.execute('SELECT 1 FROM attempts a JOIN functions f ON f.id=a.function_id '
                              'WHERE f.project_id=? AND a.context_version=? LIMIT 1', (project, version)).fetchone(): continue
                if db.execute("SELECT 1 FROM settings WHERE key LIKE ? LIMIT 1", ('boundary-repair:'+str(project)+':%',)).fetchone(): continue
                # A newly current unit header is always retained, including empty headers.
                removed = db.execute('DELETE FROM context_history WHERE project_id=? AND version=? AND version < '
                    '(SELECT MAX(h.version) FROM context_history h WHERE h.project_id=context_history.project_id AND h.unit=context_history.unit)',
                    (project, version)).rowcount
                done += removed
        maintenance.checkpoint()
        log.progress({'phase': 'Clearing unused C header history', 'done': min(start+BATCH, len(candidates)), 'total': len(candidates)})
    contents = 0
    while True:
        log.check_cancel()
        with store.tx(wait=waiting) as db:
            removed = db.execute('DELETE FROM header_blobs WHERE hash IN (SELECT hash FROM header_blobs '
                'WHERE NOT EXISTS(SELECT 1 FROM context_history WHERE header_hash=hash) LIMIT ?)', (BATCH,)).rowcount
        contents += removed
        maintenance.checkpoint()
        if not removed: break
    log(f'Header cleanup complete: {done:,} unused versions and {contents:,} unreferenced contents removed. Referenced and current headers kept.')
    return {'removed_versions': done, 'removed_contents': contents, 'headers': header_storage.stats(store)}


def compact(net, log):
    """SQLite VACUUM is atomic and interruptible, but deliberately takes the writer lock."""
    store = net.store
    path = Path(store.path)
    if not path.is_file(): raise ValueError('Compaction requires a database file on disk.')
    library=path.parent/'match-library.sqlite3'
    paths=[path,library] if library.is_file() else [path]
    size = max(p.stat().st_size for p in paths)
    if shutil.disk_usage(path.parent).free < size*2:
        raise ValueError('Free at least twice the database file size on this drive before compacting.')
    log.check_cancel()
    log.progress({'phase': 'Compacting database · writes wait until this finishes'})
    before = sum(disk_bytes(p) for p in paths)
    with store.writer_gate.hold(wait=log.check_cancel),store.lock:
        log.check_cancel()
        store.conn.set_progress_handler(lambda: int(getattr(log, 'cancel_event', None) is not None and log.cancel_event.is_set()), 1000)
        try:
            store.conn.execute('VACUUM')
        except sqlite3.OperationalError:
            log.check_cancel()
            raise
        finally:
            store.conn.set_progress_handler(None, 0)
        store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    if library.is_file():
        from .match_library import Library
        log.check_cancel()
        log.progress({'phase':'Compacting match library · library writes wait until this finishes'})
        with Library(net).connect(write=True,wait=log.check_cancel) as db:
            db.set_progress_handler(lambda: int(getattr(log,'cancel_event',None) is not None and log.cancel_event.is_set()),1000)
            try:db.execute('VACUUM')
            except sqlite3.OperationalError:
                log.check_cancel()
                raise
            finally:db.set_progress_handler(None,0)
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    after = sum(disk_bytes(p) for p in paths)
    log(f'Compaction complete: {max(0, before-after):,} bytes reclaimed. Saved work retained.')
    return {'before_bytes': before, 'after_bytes': after, 'reclaimed_bytes': max(0, before-after)}


def disk_bytes(path):
    return sum(p.stat().st_size for p in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')) if p.is_file())


def start(net, jobs, action):
    if action not in ('headers', 'compact'): raise ValueError('Choose header cleanup or database compaction.')
    with jobs._lock:
        active = next((job for job in jobs.jobs.values() if job['kind'] in ('C header cleanup', 'Database compaction') and job['state']=='running'), None)
        if active:
            expected = 'C header cleanup' if action=='headers' else 'Database compaction'
            if active['kind'] != expected: raise ValueError('Another database maintenance task is running. Wait for it or stop it first.')
            jobs.promote(active['id'])
            return active['id']
        def work(log):
            from .maintenance import slot
            with slot(log.cancel_event) as acquired:
                if not acquired: log.check_cancel()
                return prune(net, log) if action == 'headers' else compact(net, log)
        return jobs.start('C header cleanup' if action=='headers' else 'Database compaction', work,
                          cancel_message='Database maintenance stopped. Completed cleanup is retained; interrupted compaction rolls back.')
