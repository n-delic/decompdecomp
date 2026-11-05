"""Explicit payload backfill, optional failed-source retention and bounded history pages."""
import json
import time

BATCH=50
KEY='database-source-retention'


def stats(net):
    store=net.store
    page=store.one('PRAGMA page_size')[0]
    count=store.one('PRAGMA page_count')[0]
    free=store.one('PRAGMA freelist_count')[0]
    blobs=store.one('SELECT COUNT(*) contents,COALESCE(SUM(raw_bytes),0) logical_bytes,COALESCE(SUM(length(payload)),0) stored_bytes FROM text_payloads')
    row=store.one('SELECT value FROM settings WHERE key=?',KEY)
    return {**dict(blobs),'allocated_bytes':page*count,'free_bytes':page*free,
            'retention':json.loads(row['value']) if row else {'days':90,'automatic':False},
            'history_plan':[r['detail'] for r in store.all('EXPLAIN QUERY PLAN SELECT id,score,compiled,created_at FROM main.attempts WHERE function_id=? AND id<? ORDER BY id DESC LIMIT 101',0,9223372036854775807)]}


def options(value):
    days=value.get('days',90);automatic=value.get('automatic',False)
    if type(days) is not int or not 1<=days<=3650 or type(automatic) is not bool:
        raise ValueError('Choose 1–3650 days and whether automatic failed-source cleanup is enabled.')
    return {'days':days,'automatic':automatic}


def save(net,value):
    value=options(value)
    with net.store.tx() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(value)))
    retention=getattr(net,'database_retention',None)
    if retention is not None:retention.next_payload_check=0
    return value


def collect(net):
    with net.store.tx() as db:
        return db.execute('DELETE FROM text_payloads WHERE hash NOT IN '
            '(SELECT source_hash FROM main.attempts WHERE source_hash IS NOT NULL UNION '
            'SELECT evidence_hash FROM main.repair_examples WHERE evidence_hash IS NOT NULL)').rowcount


def pack(net,log):
    from .maintenance import checkpoint
    done=0
    for table,field,ref in (('attempts','source','source_hash'),('repair_examples','evidence','evidence_hash')):
        cursor=0
        while True:
            log.check_cancel()
            rows=net.store.all(f'SELECT id FROM main.{table} WHERE id>? AND {ref} IS NULL AND {field} IS NOT NULL ORDER BY id LIMIT ?',cursor,BATCH)
            if not rows:break
            log.progress({'phase':'Packing '+table,'done':done,'function':str(rows[0]['id'])})
            for row in rows:
                log.check_cancel();cursor=row['id']
                with net.store.tx() as db:
                    db.execute(f'UPDATE {table} SET {field}={field} WHERE id=? AND {ref} IS NULL',(cursor,))
                done+=1
            checkpoint()
    removed=collect(net)
    with net.store.lock:
        net.store.conn.execute('PRAGMA analysis_limit=1000')
        net.store.conn.execute('PRAGMA optimize')
    log(f'Packed {done:,} records; removed {removed:,} unused shared payloads. Compact the database to return free pages to disk.')
    return {'packed':done,**stats(net)}


def _protection(store):
    # Keep every foreign-key-referenced attempt, even if a newer feature adds another evidence table.
    clauses=[]
    for row in store.all("SELECT name FROM sqlite_master WHERE type='table'"):
        name=row['name'];quoted='"'+name.replace('"','""')+'"'
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
        eligibility=("SELECT a.id FROM main.attempts a JOIN functions f ON f.id=a.function_id "
            "WHERE a.id>? AND a.created_at<? AND a.compiled=0 AND a.score IS NULL "
            "AND (a.source_hash IS NOT NULL OR a.source IS NOT NULL) "
            "AND COALESCE(a.source_hash,payload_digest(a.source)) IS NOT payload_digest(f.best_source) "
            "AND COALESCE(a.source_hash,payload_digest(a.source)) IS NOT payload_digest(f.matched_source) "
            "AND a.id!=(SELECT MAX(t.id) FROM main.attempts t WHERE t.function_id=a.function_id AND t.agent_id IS a.agent_id) "
            "AND COALESCE(a.source_hash,payload_digest(a.source)) IS NOT payload_digest(f.seed) "
            "AND "+protection)
        # Select and clear one bounded batch in the same transaction. Protection is evaluated
        # once per batch and cannot race with a new capture or a current-source update.
        with net.store.tx() as db:
            rows=db.execute(eligibility+" ORDER BY a.id LIMIT ?",(cursor,cutoff,BATCH)).fetchall()
            for row in rows:
                log.check_cancel();cursor=row['id']
                db.execute('UPDATE attempts SET source_hash=NULL,source=NULL WHERE id=?',(cursor,))
        if not rows:break
        removed+=len(rows)
        log.progress({'phase':'Removing old failed-compile source payloads','done':removed})
        checkpoint()
    collect(net)
    return {'removed_sources':removed,**stats(net)}


def history(net,slug,function,before=0,attempt=None):
    fn=net._function(function,slug)
    if attempt is not None:
        row=net.store.one('SELECT id,source FROM attempts WHERE id=? AND function_id=?',attempt,fn['id'])
        if row is None:raise ValueError('This attempt is not part of the selected function.')
        return dict(row)
    rows=net.store.all('SELECT t.id,t.score,t.compiled,t.created_at,a.name agent,'
        '(t.source_hash IS NOT NULL OR t.source IS NOT NULL) source_available FROM main.attempts t '
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
