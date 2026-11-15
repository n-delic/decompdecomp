"""Initialize project-owned session history without moving the shared helper registry."""


def prepare(net, project):
    store = net.project_store(project)
    ready = net.__dict__.setdefault('_session_storage_ready', set())
    if store in ready:return store
    from .agents import SCHEMA
    from .session_history import prepare as revisions
    # The database writer already serializes schema initialization. A separate
    # initializer mutex would invert the order when called inside a transaction.
    with store.tx() as db:
        if store in ready:return store
        # Helper configuration/identity stays in the catalog; only sessions move.
        for statement in SCHEMA[1:]:db.execute(statement)
        if 'usage_coverage' not in {r[1] for r in db.execute('PRAGMA table_info(helper_sessions)')}:
            db.execute("ALTER TABLE helper_sessions ADD COLUMN usage_coverage TEXT NOT NULL DEFAULT 'unknown'")
        columns = {r[1] for r in db.execute('PRAGMA table_info(helper_sessions)')}
        for column in ('account_id', 'account_name'):
            if column not in columns:
                db.execute(f"ALTER TABLE helper_sessions ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        revisions(db)
        store.after_commit(lambda: ready.add(store))
    return store
