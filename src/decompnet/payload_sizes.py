"""Small transactional size records; legacy inventories run only in explicit maintenance."""
import json

BATCH=256
BATCH_BYTES=2*1024*1024
KEY='payload-sizes-cursor'


def install(conn):
    # Do not scan old blobs while opening the app. Older writers also run these SQL
    # triggers, so new content and garbage collection keep the inventory accurate.
    conn.execute('CREATE TABLE IF NOT EXISTS payload_sizes(identity INTEGER PRIMARY KEY,hash TEXT UNIQUE NOT NULL,raw_bytes INTEGER NOT NULL,stored_bytes INTEGER NOT NULL)')
    conn.execute('CREATE TRIGGER IF NOT EXISTS payload_sizes_insert AFTER INSERT ON text_payloads BEGIN '
                 'INSERT OR REPLACE INTO payload_sizes VALUES(NEW.rowid,NEW.hash,NEW.raw_bytes,length(NEW.payload)); END')
    conn.execute('CREATE TRIGGER IF NOT EXISTS payload_sizes_update AFTER UPDATE ON text_payloads BEGIN '
                 'DELETE FROM payload_sizes WHERE identity=OLD.rowid; '
                 'INSERT OR REPLACE INTO payload_sizes VALUES(NEW.rowid,NEW.hash,NEW.raw_bytes,length(NEW.payload)); END')
    conn.execute('CREATE TRIGGER IF NOT EXISTS payload_sizes_delete AFTER DELETE ON text_payloads BEGIN '
                 'DELETE FROM payload_sizes WHERE identity=OLD.rowid; END')


def stats(store):
    # COUNT uses the existing narrow hash index, not pages holding compressed blobs.
    row=store.one('SELECT COUNT(*) contents,COALESCE(SUM(raw_bytes),0) logical_bytes,'
                  'COALESCE(SUM(stored_bytes),0) stored_bytes,'
                  '(SELECT COUNT(*) FROM text_payloads) total FROM payload_sizes')
    return {key:row[key] for key in ('contents','logical_bytes','stored_bytes')} | {'complete':row['contents']==row['total']}


def backfill(store,log):
    from .app.maintenance import checkpoint
    while True:
        log.check_cancel()
        log.progress({'phase':'Indexing shared content sizes','done':0,'total':None})
        # Cursor and batch commit together. Triggers cover insertions behind the
        # cursor, replacements and deletions, including from another connection.
        with store.tx(wait=log.check_cancel) as db:
            row=db.execute('SELECT value FROM settings WHERE key=?',(KEY,)).fetchone()
            state=json.loads(row['value']) if row else {'cursor':-9223372036854775808}
            if state.get('done'):return
            page=db.execute('SELECT rowid,length(payload) FROM text_payloads WHERE rowid>? ORDER BY rowid LIMIT ?',
                            (state['cursor'],BATCH)).fetchall()
            ids=[];used=0
            for record in page:
                if ids and used+record[1]>BATCH_BYTES:break
                ids.append(record[0]);used+=record[1]
            if ids:
                log.check_cancel()
                db.execute('INSERT OR REPLACE INTO payload_sizes SELECT rowid,hash,raw_bytes,length(payload) FROM text_payloads '
                           'WHERE rowid IN (SELECT value FROM json_each(?))',(json.dumps(ids),))
                state={'cursor':ids[-1]}
            else:state={'done':True}
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(state)))
        checkpoint()
        if state.get('done'):return
