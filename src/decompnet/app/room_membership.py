"""Durable catalog-to-project membership effects; legacy stores stay one transaction."""
import json
import time
import uuid

SCHEMA = '''CREATE TABLE IF NOT EXISTS room_membership_effects(
 id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, detail TEXT NOT NULL,
 agents TEXT NOT NULL, created_at REAL NOT NULL)'''
RECEIPT = 'room-membership-applied:'


def enqueue(net, store, db, project_id, detail, agents=()):
    """Inside the catalog identity transaction: never acquire a project writer here."""
    if store is net.store:
        apply(net, db, project_id, detail, agents)
        return
    db.execute(SCHEMA)
    db.execute('CREATE INDEX IF NOT EXISTS idx_room_membership_project ON room_membership_effects(project_id,created_at,id)')
    db.execute('INSERT INTO room_membership_effects VALUES(?,?,?,?,?)',
               (uuid.uuid4().hex, project_id, detail, json.dumps(list(agents)), time.time()))


def apply(net, db, project_id, detail, agents):
    if agents:
        db.execute('DELETE FROM claims WHERE agent_id IN (SELECT value FROM json_each(?)) '
                   'AND function_id IN (SELECT id FROM functions WHERE project_id=?)',
                   (json.dumps(list(agents)), project_id))
    net._event(db, project_id, None, 'collab', detail)


def drain(net, store, project_id):
    """Commit project effects before acknowledging; receipts make replay idempotent."""
    from ..store import in_transaction

    catalog = net.store
    if store is catalog or in_transaction():
        return  # a later room read/restart delivers after the caller's transaction
    if not catalog.one("SELECT 1 FROM sqlite_master WHERE name='room_membership_effects'"):
        return
    for item in catalog.all('SELECT * FROM room_membership_effects WHERE project_id=? ORDER BY created_at,id', project_id):
        receipt = RECEIPT + item['id']
        with store.tx() as db:
            if not db.execute('SELECT 1 FROM settings WHERE key=?', (receipt,)).fetchone():
                # A missing project must leave its handoff queued, never acknowledge
                # effects into an empty replacement file.
                if not db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
                    from ..network import NetworkError
                    raise NetworkError('Room project is missing from its database. Restore it from backup.')
                apply(net, db, project_id, item['detail'], json.loads(item['agents']))
                db.execute('INSERT INTO settings(key,value) VALUES(?,?)', (receipt, 'true'))
        with catalog.tx() as db:
            db.execute('DELETE FROM room_membership_effects WHERE id=? AND project_id=?', (item['id'], project_id))
        # Keep the small receipt: another process may already have read the same
        # queued item before acknowledgement. Removing it here would allow replay.
