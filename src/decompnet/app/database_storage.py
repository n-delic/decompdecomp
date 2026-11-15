"""Explicit payload backfill, optional failed-source retention and bounded history pages."""
import json
import time
import zlib

BATCH=50
BATCH_BYTES=2*1024*1024
KEY='database-source-retention'


def stats(net):
    from pathlib import Path

    from ..repair_corpus import read_options
    from .storage_files import all_files
    store=net.store
    complete=True
    totals = dict.fromkeys(('contents','logical_bytes','stored_bytes','allocated_bytes','free_bytes'),0)
    for selected in all_files(net):
        page=selected.one('PRAGMA page_size')[0]
        totals['allocated_bytes'] += page*selected.one('PRAGMA page_count')[0]
        totals['free_bytes'] += page*selected.one('PRAGMA freelist_count')[0]
        from ..payload_sizes import stats as payload_stats
        blobs=payload_stats(selected)
        complete = complete and blobs['complete']
        for key in ('contents','logical_bytes','stored_bytes'):totals[key] += blobs[key]
    row=store.one('SELECT value FROM settings WHERE key=?',KEY)
    path=Path(store.conn.repair_archive.path)
    return {'statistics_complete':complete,'repair_archive_bytes':path.stat().st_size if path.is_file() else 0,**totals,
            'repair_evidence':read_options(store),
            'retention':json.loads(row['value']) if row else {'days':0,'automatic':False},
            'history_plan':[r['detail'] for r in store.all('EXPLAIN QUERY PLAN SELECT id,score,compiled,created_at FROM main.attempts WHERE function_id=? AND id<? ORDER BY id DESC LIMIT 101',0,9223372036854775807)]}


def options(value):
    days=value.get('days',0);automatic=value.get('automatic',False)
    if type(days) is not int or not 0<=days<=3650 or type(automatic) is not bool:
        raise ValueError('Choose 0–3650 days and whether automatic failed-source cleanup is enabled.')
    return {'days':days,'automatic':automatic}


def save(net,value):
    value=options(value)
    with net.store.tx() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(value)))
    retention=getattr(net,'database_retention',None)
    if retention is not None:retention.next_payload_check=0
    return value


def collect(net,log=None):
    from ..payload_links import collect as collect_payloads
    from .storage_files import all_files
    return sum(collect_payloads(store,log) for store in all_files(net))


# Automatic collection of unused shared text. Superseded payloads (an older version's progress manifests above all:
# 17 GB of one big game's database) are otherwise only removed when the user runs a storage task. It runs in the
# paced background maintenance lane, in the same 256-row writer transactions as the Settings task, and only on files
# whose link index a Settings task has completed: before that, finding what is unused means scanning every text
# column, which stays an explicit choice.
COLLECT_KIND='Collect unused shared text'
COLLECT_SECONDS=6*3600
COLLECT_STARTUP_SECONDS=1800  # leave the first half hour after opening to the work the user opened the app for
STORAGE_JOBS=('Compress candidate history','Clean old unsuccessful source','Database compaction','C header cleanup',
              'Database transcript cleanup',COLLECT_KIND)
_next_collect={}


def collect_indexed(net,log):
    """Collect in each file whose payload link index is complete; the rest wait for a Settings task."""
    from ..payload_links import collect as collect_payloads
    from ..payload_links import indexed
    from .storage_files import all_files
    removed=0
    for store in all_files(net):
        log.check_cancel()
        with store.tx(wait=log.check_cancel) as db:ready=indexed(db)
        if ready:removed+=collect_payloads(store,log)
    log(f'Removed {removed:,} unused shared payloads. Compact the database to return free pages to disk.')
    return {'removed':removed}


class _Rows(list):
    def fetchone(self):return self[0] if self else None

    def fetchall(self):return list(self)


class _Reads:
    """payload_links.indexed's connection interface over Store reads, for a check made without the writer."""
    def __init__(self,store):self.store=store

    def execute(self,sql,params=()):return _Rows(self.store.all(sql,*params))


def collect_tick(net,jobs,now):
    """Called by the retention check (database_cleanup.Retention.tick): at most every COLLECT_SECONDS, and only when
    some file's link index is complete."""
    from ..payload_links import indexed
    from .storage_files import all_files
    key=id(net)
    if key not in _next_collect:_next_collect[key]=now+COLLECT_STARTUP_SECONDS
    if jobs is None or now<_next_collect[key]:return None
    _next_collect[key]=now+COLLECT_SECONDS
    if not any(indexed(_Reads(store)) for store in all_files(net)):return None
    with jobs._lock:
        if any(j['kind'] in STORAGE_JOBS and j['state']=='running' for j in jobs.jobs.values()):return None
        def work(log):
            from .maintenance import slot
            with slot(log.cancel_event) as acquired:
                if not acquired:log.check_cancel()
                return collect_indexed(net,log)
        return jobs.start(COLLECT_KIND,work,foreground=False,
                          cancel_message='Collection stopped. Completed batches are retained; saved work remains.')


def pack(net,log):
    from pathlib import Path

    from .storage_files import all_files
    done = sum(_pack_store(net,store,log) for store in all_files(net))
    library_path=Path(net.store.path).parent/'match-library.sqlite3'
    if library_path.is_file():
        from .match_library import Library
        done += Library(net).pack(log)
    removed=collect(net,log)
    log(f'Packed {done:,} records; removed {removed:,} unused shared payloads. Compact the database to return free pages to disk.')
    return {'packed':done,**stats(net)}


def _pack_store(net,store,log):
    from ..payload_sizes import backfill
    from ..repair_archive import migrate
    backfill(store,log)
    from .maintenance import checkpoint
    done=migrate(net,log,store=store) if store.one("SELECT 1 FROM sqlite_master WHERE name='repair_examples'") else 0
    from ..large_text_storage import FIELDS, MIN_BYTES
    from ..payload_storage import digest, metadata
    def prepare(rows, field):
        values=[];blobs={}
        for row in rows:
            log.check_cancel()
            text=row[field];key=digest(text)
            if key not in blobs:blobs[key]=(key,zlib.compress(text.encode('utf-8')),len(text.encode('utf-8')))
            values.append((row['id'],text,key))
        return values,blobs.values()
    def batch(table,field,cursor,eligible):
        # Fetch lengths first: fifty unusually large records must not all enter Python
        # memory together. A single oversized record still advances the cursor.
        sizes=store.all(f'SELECT rowid AS id,length(CAST({field} AS BLOB)) size FROM main.{table} '
                            f'WHERE rowid>? AND {eligible} ORDER BY rowid LIMIT ?',cursor,BATCH)
        ids=[];used=0
        for row in sizes:
            if ids and used+row['size']>BATCH_BYTES:break
            ids.append(row['id']);used+=row['size']
        if not ids:return []
        return store.all(f'SELECT rowid AS id,{field} FROM main.{table} '
                             f'WHERE rowid IN (SELECT value FROM json_each(?)) AND {eligible} ORDER BY rowid',json.dumps(ids))
    installed={r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    # No startup scan or monolithic migration: conversion is explicit and resumable.
    for table,field,ref in ([('attempts','source','source_hash')] if 'attempts' in installed else []):
        cursor=0
        while True:
            log.check_cancel()
            rows=batch(table,field,cursor,f'{ref} IS NULL AND {field} IS NOT NULL')
            if not rows:break
            log.progress({'phase':'Packing '+table,'done':done,'function':str(rows[0]['id'])})
            values,blobs=prepare(rows,field);changed=0
            with store.tx(wait=log.check_cancel) as db:
                db.executemany('INSERT OR IGNORE INTO text_payloads VALUES(?,?,?)',blobs)
                for identity,text,key in values:
                    log.check_cancel();cursor=identity
                    stored=None if table=='attempts' else metadata(text)
                    changed+=db.execute(f'UPDATE {table} SET {field}=?,{ref}=? WHERE id=? AND {ref} IS NULL AND {field} IS ?',
                               (stored,key,cursor,text)).rowcount
            done+=changed
            checkpoint()
    installed={r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    for table,fields in FIELDS.items():
        if table not in installed:continue
        for field in fields:
            cursor=0
            eligible=f"typeof({field})='text' AND length(CAST({field} AS BLOB))>={MIN_BYTES}"
            while True:
                log.check_cancel()
                log.progress({'phase':f'Packing {table}: {field}','done':done})
                rows=batch(table,field,cursor,eligible)
                if not rows:break
                values,blobs=prepare(rows,field);changed=0
                with store.tx(wait=log.check_cancel) as db:
                    db.executemany('INSERT OR IGNORE INTO text_payloads VALUES(?,?,?)',blobs)
                    for identity,text,key in values:
                        log.check_cancel();cursor=identity
                        changed+=db.execute(f'UPDATE {table} SET {field}=? WHERE rowid=? AND {field} IS ?',
                                   (bytes.fromhex(key),cursor,text)).rowcount
                done+=changed
                checkpoint()
    log.check_cancel()
    with store.writer_gate.hold(wait=log.check_cancel),store.lock:
        store.conn.execute('PRAGMA analysis_limit=1000')
        store.conn.execute('PRAGMA optimize')
    return done


def _protection(store):
    # Preserve source referenced by evidence consumers, including future foreign-key tables.
    clauses=[]
    for row in store.all("SELECT name FROM sqlite_master WHERE type='table'"):
        name=row['name']
        # These records describe the attempt itself, rather than retaining its source
        # for another consumer. Keep their IDs/settings while expiring only source.
        if name in ('repair_attempt_settings','repair_attempt_provenance','history_rewrites','attempt_hints'):continue
        quoted='"'+name.replace('"','""')+'"'
        for column in store.all('PRAGMA table_info('+quoted+')'):
            if column['name'] in ('attempt_id','before_attempt_id','after_attempt_id','baseline_attempt_id','previous_attempt_id'):
                col='"'+column['name']+'"'
                clauses.append(f'SELECT {col} FROM {quoted} WHERE {col} IS NOT NULL')
        for fk in store.all('PRAGMA foreign_key_list('+quoted+')'):
            if fk['table']=='attempts':
                col='"'+fk['from'].replace('"','""')+'"'
                clauses.append(f'SELECT {col} FROM {quoted} WHERE {col} IS NOT NULL')
    # Some captured stages retain references inside JSON in addition to their direct foreign keys.
    clauses.append("SELECT j.atom FROM main.repair_examples r,json_tree(CASE WHEN json_valid(r.evidence) THEN r.evidence ELSE '{}' END) j "
                   "WHERE j.key IN ('attempt_id','before_attempt_id','after_attempt_id','baseline_attempt_id','previous_attempt_id') AND j.type='integer'")
    return 'a.id NOT IN ('+' UNION '.join(dict.fromkeys(clauses))+')'


def prune(net,value,log):
    if options(value)['days']==0:raise ValueError('Choose a positive age before removing old candidate source.')
    from .storage_files import operational
    results = [_prune_store(net,store,value,log) for store in operational(net)]
    collect(net,log)
    return {'removed_sources':sum(r['removed_sources'] for r in results),
            'compacted_search_details':sum(r['compacted_search_details'] for r in results),**stats(net)}


def _prune_store(net,store,value,log):
    from .maintenance import checkpoint
    cutoff=time.time()-options(value)['days']*86400
    protection=_protection(store)
    removed=0;cursor=0
    while True:
        log.check_cancel()
        # Payloads only: attempt IDs, scores, timestamps and lifetime metrics remain intact.
        eligibility=("SELECT a.id FROM main.attempts a JOIN main.functions f ON f.id=a.function_id "
            "WHERE a.id IN (SELECT value FROM json_each(?)) AND a.created_at<? AND (a.compiled=0 AND a.score IS NULL OR a.score>0 AND a.score_improved=0) "
            "AND (a.source_hash IS NOT NULL OR a.source IS NOT NULL) "
            "AND COALESCE(a.source_hash,payload_digest(a.source)) IS NOT (CASE WHEN typeof(f.best_source)='blob' THEN lower(hex(f.best_source)) ELSE payload_digest(f.best_source) END) "
            "AND COALESCE(a.source_hash,payload_digest(a.source)) IS NOT (CASE WHEN typeof(f.matched_source)='blob' THEN lower(hex(f.matched_source)) ELSE payload_digest(f.matched_source) END) "
            "AND a.id!=(SELECT MAX(t.id) FROM main.attempts t WHERE t.function_id=a.function_id AND t.agent_id IS a.agent_id) "
            "AND a.id!=COALESCE((SELECT MAX(t.id) FROM main.attempts t WHERE t.function_id=a.function_id AND t.agent_id IS a.agent_id AND t.compiled=1),-1) "
            "AND COALESCE(a.source_hash,payload_digest(a.source)) IS NOT (CASE WHEN typeof(f.seed)='blob' THEN lower(hex(f.seed)) ELSE payload_digest(f.seed) END) "
            "AND "+protection)
        # Select and clear one bounded batch in the same transaction. Protection is evaluated
        # once per batch and cannot race with a new capture or a current-source update.
        with store.tx(wait=log.check_cancel) as db:
            page=db.execute('SELECT id FROM main.attempts WHERE id>? ORDER BY id LIMIT ?',(cursor,BATCH)).fetchall()
            if not page:break
            cursor=page[-1]['id']
            rows=db.execute(eligibility+" ORDER BY a.id",(json.dumps([r['id'] for r in page]),cutoff)).fetchall()
            for row in rows:
                log.check_cancel()
                db.execute('UPDATE attempts SET source_digest=COALESCE(source_digest,source_hash,payload_digest(source)),source_hash=NULL,source=NULL WHERE id=?',(row['id'],))
        removed+=len(rows)
        log.progress({'phase':'Removing superseded unsuccessful source payloads','done':removed})
        checkpoint()
    from .search_history import prune as prune_search
    search_details=prune_search(store,log)
    return {'removed_sources':removed,'compacted_search_details':search_details}


def history(net,slug,function,before=0,attempt=None):
    fn=net._function(function,slug)
    store=net.project_store(fn['project_id'])
    if attempt is not None:
        row=store.one('SELECT id,source,source_digest FROM attempts WHERE id=? AND function_id=?',attempt,fn['id'])
        if row is None:raise ValueError('This attempt is not part of the selected function.')
        return dict(row)
    rows=store.all('SELECT t.id,t.score,t.compiled,t.created_at,t.agent_id,'
        'COALESCE(t.source_digest,t.source_hash) source_digest,(t.source_hash IS NOT NULL OR t.source IS NOT NULL) source_available FROM main.attempts t '
        'WHERE t.function_id=? AND t.id<? ORDER BY t.id DESC LIMIT 101',fn['id'],before or 9223372036854775807)
    from .project_labels import label
    return {'attempts':label(net,rows[:100],{'agent':'agent_id'}),'before':rows[99]['id'] if len(rows)>100 else None}


def start(net,jobs,action,value=None,*,foreground=True):
    if action not in ('pack','prune'):raise ValueError('Choose pack or prune.')
    rules=options(value or {})
    if action=='prune' and rules['days']==0:raise ValueError('Choose a positive age before removing old candidate source.')
    kind='Compress candidate history' if action=='pack' else 'Clean old unsuccessful source'
    with jobs._lock:
        active=next((j for j in jobs.jobs.values() if j['kind'] in
                     ('Compress candidate history','Clean old unsuccessful source') and j['state']=='running'),None)
        if active:
            if active['kind']!=kind and foreground:raise ValueError('Another candidate storage task is running. Wait for it or stop it first.')
            if foreground:jobs.promote(active['id'])
            return active['id']
        def work(log):
            from .maintenance import slot
            with slot(log.cancel_event) as acquired:
                if not acquired:log.check_cancel()
                return pack(net,log) if action=='pack' else prune(net,rules,log)
        return jobs.start(kind,work,foreground=foreground,
                          cancel_message='Storage maintenance stopped. Completed batches are retained; saved work remains.')
