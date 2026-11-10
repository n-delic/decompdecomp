"""Shared, compressed large text without changing table keys or insert column counts.

Text columns contain either legacy/small TEXT or a 32-byte binary content hash. Detail
views resolve only selected columns; metadata queries never inflate their payloads.
"""

FIELDS = {
    'auto_match_commits': ('snapshot',),
    'reuse_trials': ('feedback',),
    'compiler_trial_evidence': ('record',),
    'helper_messages': ('text',),
    'repair_attempt_settings': ('settings',),
    'progress_state': ('disk', 'projected', 'disk_manifest', 'projected_manifest'),
    'functions': ('asm', 'seed', 'best_source', 'matched_source', 'unpolished_source'),
    'repair_frontiers': ('state',),
    'verified_matches': ('details', 'source', 'header'),
    'repair_attempt_provenance': ('provenance',),
    'repair_outcome_analysis': ('report',),
}
MIN_BYTES = 512


def install(conn, tables=None):
    # A crash must not leave a visible detail view with only half its write triggers.
    conn.execute('SAVEPOINT large_text_schema')
    try:_install(conn,tables)
    except BaseException:
        conn.execute('ROLLBACK TO large_text_schema')
        raise
    finally:conn.execute('RELEASE large_text_schema')


def _install(conn, tables=None):
    from .payload_storage import Connection
    for table, fields in FIELDS.items():
        if tables is not None and table not in tables:continue
        columns = [r[1] for r in conn.execute('PRAGMA table_info('+table+')')]
        if not columns:continue
        fields = [f for f in fields if f in columns]
        expressions = []
        for column in columns:
            if column in fields:
                key = f'lower(hex({column}))'
                expressions.append(f'CASE WHEN typeof({column})=\'blob\' THEN '
                    f'payload_read({key},(SELECT payload FROM text_payloads WHERE hash={key})) '
                    f'ELSE {column} END AS {column}')
            else:expressions.append(column)
        # Reuse searches paginate by insertion order, including tables with composite keys.
        if table == 'reuse_trials':expressions.append('rowid AS rowid')
        ddl=f'CREATE VIEW packed_{table} AS SELECT '+','.join(expressions)+f' FROM main.{table}'
        old=conn.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?",('packed_'+table,)).fetchone()
        if old is None or old[0]!=ddl:
            conn.execute(f'DROP VIEW IF EXISTS packed_{table}')
            conn.execute(ddl)
        for field in fields:
            from .payload_links import install as install_links
            install_links(conn,table,field,blob=True)
            condition=f"typeof(NEW.{field})='text' AND length(CAST(NEW.{field} AS BLOB))>={MIN_BYTES}"
            put=(f'INSERT OR IGNORE INTO text_payloads SELECT payload_digest(NEW.{field}),'
                 f'payload_pack(NEW.{field}),length(CAST(NEW.{field} AS BLOB)) '
                 f'WHERE NOT EXISTS(SELECT 1 FROM text_payloads WHERE hash=payload_digest(NEW.{field}));')
            for event in ('INSERT','UPDATE'):
                conn.execute(f'CREATE TRIGGER IF NOT EXISTS large_{table}_{field}_{event.lower()} '
                    f'AFTER {event}'+(f' OF {field}' if event=='UPDATE' else '')+f' ON {table} '
                    f'WHEN {condition} BEGIN '+put+
                    f'UPDATE {table} SET {field}=unhex(payload_digest(NEW.{field})) WHERE rowid=NEW.rowid; END')
        if isinstance(conn,Connection):conn.detail_tables=frozenset(conn.detail_tables)|{table}


def references(store):
    """Raw references for garbage collection; callers hold the writer transaction."""
    installed={r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    return [f"SELECT lower(hex({field})) FROM main.{table} WHERE typeof({field})='blob'"
            for table,fields in FIELDS.items() if table in installed for field in fields]
