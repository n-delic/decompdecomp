"""Compact global notification order, independent of each project's event ID range.

Migration seeds legacy IDs before workers start. Later delivery is idempotent and
advances a source cursor in the same catalog transaction as its indexed events.
Project history stays in its owning file; this index contains only feed metadata.
"""
import threading

from ..network import NetworkError
from ..store import in_transaction

READY = 'activity-index-ready'
PREFIX = 'activity-source:'
BATCH = 256
SCHEMA = '''CREATE TABLE IF NOT EXISTS activity_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,source_id INTEGER NOT NULL,
 agent_id INTEGER,kind TEXT,detail TEXT,created_at REAL,UNIQUE(project_id,source_id));
 CREATE INDEX IF NOT EXISTS idx_activity_project ON activity_events(project_id,id);'''
COLUMNS = 'id,project_id,agent_id,kind,detail,created_at'


def bootstrap(net):
    """Migration/startup only, while workers are stopped; preserve legacy feed IDs."""
    if in_transaction():raise NetworkError('Activity migration must run outside another database transaction.')
    from .engine.gui import NOTIFY_KINDS
    with net.store.tx() as db:
        if db.execute('SELECT 1 FROM settings WHERE key=?',(READY,)).fetchone():return
        for sql in SCHEMA.split(';'):
            if sql.strip():db.execute(sql)
        maximum = 0
        for store,pids in net.project_stores():
            for pid in pids:
                last = store.one('SELECT COALESCE(MAX(id),0) n FROM events WHERE project_id=?',pid)['n']
                maximum = max(maximum,last)
                after = 0
                while True:
                    rows = store.all('SELECT '+COLUMNS+' FROM events WHERE project_id=? AND id>? AND id<=? ORDER BY id LIMIT 500',pid,after,last)
                    if not rows:break
                    for row in rows:
                        if row['kind'] in NOTIFY_KINDS:
                            db.execute('INSERT INTO activity_events VALUES(?,?,?,?,?,?,?)',
                                       (row['id'],pid,row['id'],row['agent_id'],row['kind'],row['detail'],row['created_at']))
                    after = rows[-1]['id']
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+str(pid),str(last)))
        # The old cursor may point at a routine event excluded from notifications.
        db.execute("DELETE FROM sqlite_sequence WHERE name='activity_events'")
        db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('activity_events',?)",(maximum,))
        db.execute('INSERT INTO settings VALUES(?,?)',(READY,'1'))


def sync(net, *, drain=False):
    """Bound each project's incremental read; no project writer or payload scan."""
    if in_transaction():raise NetworkError('Global activity synchronization must run outside a project transaction.')
    if not net.store.one('SELECT 1 FROM settings WHERE key=?',READY):
        raise NetworkError('The global activity index has not been migrated. Finish database migration before opening this feed.')
    from .engine.gui import NOTIFY_KINDS
    lock = net.__dict__.setdefault('_activity_delivery_lock',threading.RLock())
    with lock:
        sources = []
        for store,pids in net.project_stores():
            for pid in pids:
                key = PREFIX+str(pid)
                cursor = net.store.one('SELECT value FROM settings WHERE key=?',key)
                after = int(cursor['value']) if cursor else 0
                maximum = store.one('SELECT COALESCE(MAX(id),0) n FROM events WHERE project_id=?',pid)['n']
                sources.append([store,pid,key,after,maximum])
        while True:
            pending,advances = [],[]
            for source in sources:
                store,pid,key,after,maximum = source
                if after >= maximum:continue
                rows = store.all('SELECT '+COLUMNS+' FROM events WHERE project_id=? AND id>? AND id<=? ORDER BY id LIMIT ?',pid,after,maximum,BATCH)
                if not rows:continue
                pending.extend(row for row in rows if row['kind'] in NOTIFY_KINDS)
                source[3] = rows[-1]['id']
                advances.append((key,str(source[3])))
            if not advances:return
            pending.sort(key=lambda row:(row['created_at'] or 0,row['project_id'],row['id']))
            with net.store.tx() as db:
                db.executemany('INSERT OR IGNORE INTO activity_events(project_id,source_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,?,?)',
                               [(row['project_id'],row['id'],row['agent_id'],row['kind'],row['detail'],row['created_at']) for row in pending])
                db.executemany('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET '
                               'value=CAST(MAX(CAST(value AS INTEGER),CAST(excluded.value AS INTEGER)) AS TEXT)',advances)
            if not drain:return


def selected(net):
    return any(store is not net.store for store,_ in net.project_stores())


def _rows(net, after, last, limit, recent=False):
    # Shared identity labels remain current, and removed projects leave the feed.
    direction = 'DESC' if recent else 'ASC'
    rows = net.store.all('SELECT e.id,e.project_id,e.kind,e.detail,e.created_at,a.name AS agent,p.slug,p.name AS project '
        'FROM activity_events e JOIN projects p ON p.id=e.project_id LEFT JOIN agents a ON a.id=e.agent_id '
        "WHERE e.id>? AND e.id<=? AND COALESCE(a.name,'') NOT IN ('progress-judge','@progress-judge') "
        f'ORDER BY e.id {direction} LIMIT ?',after,last,limit)
    return [{k:row[k] for k in ('id','kind','detail','created_at','agent','slug','project')} for row in rows]


def events_after(net, after, limit):
    sync(net,drain=after is None)
    row = net.store.one("SELECT seq FROM sqlite_sequence WHERE name='activity_events'")
    last = row['seq'] if row else 0
    if after is None:return {'last':last,'events':[]}
    rows = _rows(net,after,last,limit)
    return {'last':rows[-1]['id'] if len(rows)==limit else last,'events':rows}


def recent_events(net, limit):
    sync(net)
    return _rows(net,0,9223372036854775807,limit,recent=True)
