"""Explicit payload backfill, optional failed-source retention and bounded history pages."""
import json
import time
import zlib

BATCH=50
BATCH_BYTES=2*1024*1024
KEY='database-source-retention'


def stats(net):
    from ..repair_corpus import read_options
    store=net.store
    page=store.one('PRAGMA page_size')[0]
    count=store.one('PRAGMA page_count')[0]
    free=store.one('PRAGMA freelist_count')[0]
    blobs=store.one('SELECT COUNT(*) contents,COALESCE(SUM(raw_bytes),0) logical_bytes,COALESCE(SUM(length(payload)),0) stored_bytes FROM text_payloads')
    row=store.one('SELECT value FROM settings WHERE key=?',KEY)
    from ..repair_archive import archive_path
    path=archive_path(store.path)
    return {'repair_archive_bytes':path.stat().st_size if path.is_file() else 0,
            **dict(blobs),'allocated_bytes':page*count,'free_bytes':page*free,
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
    return collect_payloads(net.store,log)


def pack(net,log):
    from ..repair_archive import migrate
    from .maintenance import checkpoint
    done=migrate(net,log)
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
        sizes=net.store.all(f'SELECT rowid AS id,length(CAST({field} AS BLOB)) size FROM main.{table} '
                            f'WHERE rowid>? AND {eligible} ORDER BY rowid LIMIT ?',cursor,BATCH)
        ids=[];used=0
        for row in sizes:
            if ids and used+row['size']>BATCH_BYTES:break
            ids.append(row['id']);used+=row['size']
        if not ids:return []
        return net.store.all(f'SELECT rowid AS id,{field} FROM main.{table} '
                             f'WHERE rowid IN (SELECT value FROM json_each(?)) AND {eligible} ORDER BY rowid',json.dumps(ids))
    # No startup scan or monolithic migration: conversion is explicit and resumable.
    for table,field,ref in (('attempts','source','source_hash'),):
        cursor=0
        while True:
            log.check_cancel()
            rows=batch(table,field,cursor,f'{ref} IS NULL AND {field} IS NOT NULL')
            if not rows:break
            log.progress({'phase':'Packing '+table,'done':done,'function':str(rows[0]['id'])})
            values,blobs=prepare(rows,field);changed=0
            with net.store.tx(wait=log.check_cancel) as db:
                db.executemany('INSERT OR IGNORE INTO text_payloads VALUES(?,?,?)',blobs)
                for identity,text,key in values:
                    log.check_cancel();cursor=identity
                    stored=None if table=='attempts' else metadata(text)
                    changed+=db.execute(f'UPDATE {table} SET {field}=?,{ref}=? WHERE id=? AND {ref} IS NULL AND {field} IS ?',
                               (stored,key,cursor,text)).rowcount
            done+=changed
            checkpoint()
    installed={r['name'] for r in net.store.all("SELECT name FROM sqlite_master WHERE type='table'")}
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
                with net.store.tx(wait=log.check_cancel) as db:
                    db.executemany('INSERT OR IGNORE INTO text_payloads VALUES(?,?,?)',blobs)
                    for identity,text,key in values:
                        log.check_cancel();cursor=identity
                        changed+=db.execute(f'UPDATE {table} SET {field}=? WHERE rowid=? AND {field} IS ?',
                                   (bytes.fromhex(key),cursor,text)).rowcount
                done+=changed
                checkpoint()
    from pathlib import Path
    library_path=Path(net.store.path).parent/'match-library.sqlite3'
    if library_path.is_file():
        from .match_library import Library
        done+=Library(net).pack(log)
    log.check_cancel()
    log.progress({'phase':'Collecting unused shared text','done':done})
    removed=collect(net,log)
    with net.store.writer_gate.hold(wait=log.check_cancel),net.store.lock:
        net.store.conn.execute('PRAGMA analysis_limit=1000')
        net.store.conn.execute('PRAGMA optimize')
    log(f'Packed {done:,} records; removed {removed:,} unused shared payloads. Compact the database to return free pages to disk.')
    return {'packed':done,**stats(net)}


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
    from .maintenance import checkpoint
    cutoff=time.time()-options(value)['days']*86400
    protection=_protection(net.store)
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
        with net.store.tx(wait=log.check_cancel) as db:
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
    search_details=prune_search(net.store,log)
    collect(net,log)
    return {'removed_sources':removed,'compacted_search_details':search_details,**stats(net)}


def history(net,slug,function,before=0,attempt=None):
    fn=net._function(function,slug)
    store=net.project_store(fn['project_id'])
    if attempt is not None:
        row=store.one('SELECT id,source,source_digest FROM attempts WHERE id=? AND function_id=?',attempt,fn['id'])
        if row is None:raise ValueError('This attempt is not part of the selected function.')
        return dict(row)
    rows=store.all('SELECT t.id,t.score,t.compiled,t.created_at,a.name agent,'
        'COALESCE(t.source_digest,t.source_hash) source_digest,(t.source_hash IS NOT NULL OR t.source IS NOT NULL) source_available FROM main.attempts t '
        'LEFT JOIN agents a ON a.id=t.agent_id WHERE t.function_id=? AND t.id<? ORDER BY t.id DESC LIMIT 101',fn['id'],before or 9223372036854775807)
    return {'attempts':[dict(r) for r in rows[:100]],'before':rows[99]['id'] if len(rows)>100 else None}


def start(net,jobs,action,value=None,*,foreground=True):
    if action not in ('pack','prune'):raise ValueError('Choose pack or prune.')
    rules=options(value or {})
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
