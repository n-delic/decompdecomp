"""Initialize project-owned session history without moving the shared helper registry."""
import threading


def prepare(net, project):
    store = net.project_store(project)
    lock = net.__dict__.setdefault('_session_storage_lock', threading.RLock())
    with lock:
        ready = net.__dict__.setdefault('_session_storage_ready', set())
        if store in ready:return store
        from .agents import SCHEMA
        from .session_history import prepare as revisions
        with store.tx() as db:
            # Helper configuration/identity stays in the catalog; only sessions move.
            for statement in SCHEMA[1:]:db.execute(statement)
            if 'usage_coverage' not in {r[1] for r in db.execute('PRAGMA table_info(helper_sessions)')}:
                db.execute("ALTER TABLE helper_sessions ADD COLUMN usage_coverage TEXT NOT NULL DEFAULT 'unknown'")
            revisions(db)
        ready.add(store)
    return store
