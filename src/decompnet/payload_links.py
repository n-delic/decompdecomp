"""Indexed shared-payload reachability; explicit, resumable backfill never runs at startup.

Links change atomically with their owning records, including writes from older app
processes. Garbage collection never holds the writer while scanning every text table.
"""
import json

BATCH = 256


def install(conn, table, field, *, blob=False):
    conn.execute('SAVEPOINT payload_link_schema')
    try:_install(conn,table,field,blob=blob)
    except BaseException:
        conn.execute('ROLLBACK TO payload_link_schema')
        raise
    finally:conn.execute('RELEASE payload_link_schema')


def _install(conn, table, field, *, blob=False):
    conn.execute('CREATE TABLE IF NOT EXISTS payload_links(owner TEXT NOT NULL,record_id INTEGER NOT NULL,'
                 'hash TEXT NOT NULL,PRIMARY KEY(owner,record_id))')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_payload_links_hash ON payload_links(hash)')
    owner=table+'.'+field
    name='payload_link_'+table+'_'+field
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",(name+'_insert',)).fetchone():return
    conn.execute('DELETE FROM settings WHERE key=?',('payload-links:'+owner,))
    expression=f'lower(hex({field}))' if blob else field
    condition=f"typeof({field})='blob'" if blob else f'{field} IS NOT NULL'
    aliases=[r[1] for r in conn.execute('PRAGMA table_info('+table+')') if r[5]==1 and r[2].upper()=='INTEGER']
    for event in ('INSERT','UPDATE OF '+','.join(dict.fromkeys([field,'rowid',*aliases]))):
        suffix='insert' if event=='INSERT' else 'update'
        conn.execute(f'CREATE TRIGGER {name}_{suffix} AFTER {event} ON {table} BEGIN '+
                     (f"DELETE FROM payload_links WHERE owner='{owner}' AND record_id IN (NEW.rowid,OLD.rowid); " if suffix=='update' else
                      f"DELETE FROM payload_links WHERE owner='{owner}' AND record_id=NEW.rowid; ")+
                     f"INSERT INTO payload_links SELECT '{owner}',NEW.rowid,{expression} FROM {table} WHERE rowid=NEW.rowid AND {condition}; END")
    conn.execute(f'CREATE TRIGGER {name}_delete AFTER DELETE ON {table} BEGIN '
                 f"DELETE FROM payload_links WHERE owner='{owner}' AND record_id=OLD.rowid; END")


def fields(store):
    from .large_text_storage import FIELDS
    tables={r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    candidates=[('attempts','source_hash',False),('repair_examples','evidence_hash',False)]
    candidates.extend((table,field,True) for table,columns in FIELDS.items() for field in columns)
    return [(table,field,blob) for table,field,blob in candidates if table in tables and
            field in {r['name'] for r in store.all('PRAGMA table_info('+table+')')}]


def collect(store, log=None):
    from .app.maintenance import checkpoint
    check=getattr(log,'check_cancel',lambda:None)
    progress=getattr(log,'progress',lambda *_:None)
    # A setting cursor and each link batch commit together. Concurrent updates run
    # the triggers; a crash or cancellation can resume without a stale snapshot.
    owners=fields(store)
    for table,field,blob in owners:
        owner=table+'.'+field
        key='payload-links:'+owner
        with store.tx(wait=check) as db:install(db,table,field,blob=blob)
        row=store.one('SELECT value FROM settings WHERE key=?',key)
        state=json.loads(row['value']) if row else {'cursor':-9223372036854775808}
        while not state.get('done'):
            check()
            progress({'phase':'Indexing shared text references: '+owner})
            with store.tx(wait=check) as db:
                expr=f'lower(hex({field}))' if blob else field
                valid=f"typeof({field})='blob'" if blob else f'{field} IS NOT NULL'
                rows=db.execute(f'SELECT rowid AS identity,CASE WHEN {valid} THEN {expr} END AS hash '
                                f'FROM main.{table} WHERE rowid>? ORDER BY rowid LIMIT ?',
                                (state['cursor'],BATCH)).fetchall()
                for record in rows:
                    db.execute('DELETE FROM payload_links WHERE owner=? AND record_id=?',(owner,record['identity']))
                    if record['hash'] is not None:
                        db.execute('INSERT INTO payload_links VALUES(?,?,?)',(owner,record['identity'],record['hash']))
                state={'cursor':rows[-1]['identity'],'done':False} if rows else {'done':True}
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(state)))
            checkpoint()
        # INSERT OR REPLACE can silently delete a row without DELETE triggers.
        # Sweep old links by indexed owner/row ID before collecting any payloads.
        link_cursor=-9223372036854775808
        while True:
            check()
            progress({'phase':'Checking shared text references: '+owner})
            with store.tx(wait=check) as db:
                links=db.execute('SELECT record_id,hash FROM payload_links WHERE owner=? AND record_id>? '
                                 'ORDER BY record_id LIMIT ?',(owner,link_cursor,BATCH)).fetchall()
                expr=f'lower(hex({field}))' if blob else field
                valid=f"typeof({field})='blob'" if blob else f'{field} IS NOT NULL'
                for link in links:
                    db.execute('DELETE FROM payload_links WHERE owner=? AND record_id=? AND NOT EXISTS('
                               f'SELECT 1 FROM main.{table} WHERE rowid=? AND {valid} AND {expr}=?)',
                               (owner,link['record_id'],link['record_id'],link['hash']))
                if links:link_cursor=links[-1]['record_id']
            checkpoint()
            if not links:break
    schema=None
    removed=0;cursor=-9223372036854775808
    while True:
        check()
        progress({'phase':'Collecting unused shared text','done':removed})
        with store.tx(wait=check) as db:
            current_schema=db.execute('PRAGMA schema_version').fetchone()[0]
            changed_schema=(fields(store)!=owners if schema is None else current_schema!=schema)
            if changed_schema:break
            schema=current_schema
            # Page through all payload IDs, not just orphans. A database with no
            # orphans must also release the writer after bounded work.
            rows=db.execute('SELECT rowid AS identity,hash FROM text_payloads WHERE rowid>? '
                            'ORDER BY rowid LIMIT ?',(cursor,BATCH)).fetchall()
            if rows:
                cursor=rows[-1]['identity']
                for row in rows:
                    removed+=db.execute('DELETE FROM text_payloads WHERE rowid=? AND NOT EXISTS('
                                        'SELECT 1 FROM payload_links WHERE hash=?)',
                                        (row['identity'],row['hash'])).rowcount
        checkpoint()
        if not rows:break
    return removed+collect(store,log) if changed_schema else removed
