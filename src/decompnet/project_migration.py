"""Restartable copy/validate/publish upgrade, before any project workers start.

The catalog keeps its inode. A durable snapshot and journal survive interruption;
legacy operational writers are fenced, and file locations publish only after all
copies pass validation. Missing files never fall back to the catalog.
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from . import project_partition as ownership
from .header_storage import Codec
from .large_text_storage import FIELDS
from .payload_storage import unpack
from .shard_ids import KEY as NAMESPACE
from .shard_ids import STABLE, bounds, ddl

KEY = 'project-databases-v1'
BATCH = 256


class MigrationConnection(sqlite3.Connection):
    project_namespace = None


def raw(db,sql,args=()):return sqlite3.Connection.execute(db,sql,args)


def _file_hash(path):
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def durable(path):
    # Windows FlushFileBuffers needs write access, even after SQLite has closed the snapshot.
    with Path(path).open('r+b') as handle:os.fsync(handle.fileno())
    if os.name != 'nt':
        descriptor = os.open(str(Path(path).parent),os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)


def state(store):
    row = store.one('SELECT value FROM settings WHERE key=?',KEY)
    if row is None:return None
    try:
        value = json.loads(row['value'])
        if value['version']!=1 or value['phase'] not in ('copying','retiring','ready') or not isinstance(value['projects'],dict):raise ValueError()
        return value
    except (ValueError,KeyError,TypeError):
        raise ValueError('The project database journal is invalid. Restore a consistent database backup before reopening the app.') from None


def save(store,value):
    with store.tx() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(value)))


def _settings(source,target,pid,selector,transcripts=False):
    keys = [key for owner in (pid,'each') for key in selector.keys.get(owner,[])
            if (key.partition(':')[0] in ownership.TRANSCRIPT_KEYS)==transcripts]
    for start in range(0,len(keys),BATCH):
        rows = source.execute('SELECT key,value FROM settings WHERE key IN (SELECT value FROM json_each(?))',
                              (json.dumps(keys[start:start+BATCH]),))
        target.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',rows)


def _copy_rows(source,target,table,where='1',args=()):
    quoted = ownership.quote(table)
    # Keep rowids too: some composite-key search cursors use insertion order.
    cursor = source.execute('SELECT rowid,* FROM '+quoted+' WHERE '+where,args)
    columns = ['rowid',*[column[1] for column in source.execute('PRAGMA table_info('+quoted+')')]]
    insert = 'INSERT INTO '+quoted+'('+','.join(ownership.quote(c) for c in columns)+') VALUES('+','.join('?' for _ in columns)+')'
    count = 0
    while rows := cursor.fetchmany(BATCH):
        target.executemany(insert,rows)
        count += len(rows)
    # Query in source rowid order on both sides; source query plans can otherwise
    # use different indexes for the same predicate.
    expected = hashlib.sha256()
    for row in source.execute('SELECT rowid,* FROM '+quoted+' WHERE '+where+' ORDER BY rowid',args):
        expected.update(repr(tuple(row)).encode())
    actual = hashlib.sha256()
    for row in target.execute('SELECT rowid,* FROM '+quoted+' ORDER BY rowid'):actual.update(repr(tuple(row)).encode())
    if expected.digest()!=actual.digest():raise ValueError('Database migration row validation failed for '+table+'.')
    return count


def _payloads(source,target):
    installed = ownership.tables(target)
    references = []
    for table,fields in FIELDS.items():
        if table not in installed:continue
        columns = {c[1] for c in target.execute('PRAGMA table_info('+ownership.quote(table)+')')}
        references += [f'SELECT lower(hex({field})) FROM {table} WHERE typeof({field})=\'blob\'' for field in fields if field in columns]
    for table,field in (('attempts','source_hash'),('repair_examples','evidence_hash')):
        if table in installed and field in {c[1] for c in target.execute('PRAGMA table_info('+table+')')}:
            references.append(f'SELECT {field} FROM {table} WHERE {field} IS NOT NULL')
    if references:
        cursor = target.execute(' UNION '.join(references))
        while hashes := cursor.fetchmany(BATCH):
            for digest, in hashes:
                row = source.execute('SELECT * FROM text_payloads WHERE hash=?',(digest,)).fetchone()
                if row is None:raise ValueError('Database migration found a missing compressed payload. Restore a consistent backup.')
                text = unpack(row[0],row[1])
                if len(text.encode()) != row[2]:raise ValueError('Database payload size validation failed.')
                target.execute('INSERT OR IGNORE INTO text_payloads VALUES(?,?,?)',tuple(row))
    if 'context_history' in installed:
        for digest, in target.execute('SELECT DISTINCT header_hash FROM context_history WHERE header_hash IS NOT NULL'):
            row = source.execute('SELECT * FROM header_blobs WHERE hash=?',(digest,)).fetchone()
            if row is None:raise ValueError('Database migration found a missing header. Restore a consistent backup.')
            if len(Codec.unpack(row[0],row[1]).encode()) != row[2]:raise ValueError('Database header size validation failed.')
            target.execute('INSERT INTO header_blobs VALUES(?,?,?)',tuple(row))


def build(source,path,project,selector,*,transcripts=False):
    """An interrupted unpublished staging file can be discarded and rebuilt."""
    from .shard_ids import seed
    pid,slug = project['id'],project['slug']
    path = Path(path)
    staging = Path(str(path)+'.building')
    for suffix in ('','-wal','-shm'):Path(str(staging)+suffix).unlink(missing_ok=True)
    target = sqlite3.connect(staging,isolation_level=None,factory=MigrationConnection)
    try:
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('PRAGMA synchronous=FULL')
        target.execute('BEGIN IMMEDIATE')
        schema = source.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        include = ({'settings','read_revisions','text_payloads'} | ownership.TRANSCRIPTS if transcripts else
                   set(ownership.tables(source))-ownership.TRANSCRIPTS)
        if not transcripts:include |= {name for kind,name,table,sql in schema if kind=='view' and name!='packed_helper_messages'}
        for kind,name,table,sql in schema:
            if kind=='table' and name in include:target.execute(ddl(sql))
        if transcripts:
            for table in ownership.TRANSCRIPTS & include & ownership.tables(source).keys():
                where,args = ownership.predicate(source,table,pid,slug)
                _copy_rows(source,target,table,where,args)
        else:
            for table in sorted(include & ownership.tables(source).keys() & (ownership.SCOPED | ownership.COUNTERS | {'projects'})):
                where,args = ownership.predicate(source,table,pid,slug)
                _copy_rows(source,target,table,where,args)
        if not transcripts:
            from .shard_ids import BLOCK, generated_tables
            for table in generated_tables(target):
                if target.execute('SELECT 1 FROM '+ownership.quote(table)+' WHERE id>=? LIMIT 1',(BLOCK,)).fetchone():
                    raise ValueError('Legacy row IDs exceed the supported migration range. The original database and backup are retained.')
        _settings(source,target,pid,selector,transcripts)
        _payloads(source,target)
        if transcripts:target.execute("INSERT INTO settings VALUES('transcript-project-id',?)",(str(pid),))
        # Install behavior only after copying, so triggers cannot mutate copied
        # evidence, counters or snapshots while their rows are being validated.
        if not transcripts:
            for kind,name,table,sql in schema:
                if kind=='table' or name.startswith('project_store_guard_'):continue
                if table not in include or name=='packed_helper_messages':continue
                target.execute(sql)
            # Seed before any runtime insert; explicit legacy IDs stay unchanged.
            target.project_namespace = pid
            seed(target)
            target.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(NAMESPACE,str(pid)))
            target.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(STABLE,'1'))
        target.execute('COMMIT')
        if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database migration integrity validation failed.')
        if target.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Database migration foreign-key validation failed.')
        target.close();target = None
        durable(staging)
        os.replace(staging,path);durable(path)
    finally:
        if target is not None:target.close()


def _fence(store):
    tables = ownership.tables(store.conn)
    with store.tx() as db:
        for table in sorted(tables.keys() & (ownership.SCOPED | ownership.TRANSCRIPTS | {'projects'})):
            for event in ('INSERT','UPDATE','DELETE'):
                db.execute(f'CREATE TRIGGER IF NOT EXISTS project_store_guard_{table}_{event} BEFORE {event} ON {ownership.quote(table)} '
                           "WHEN storage_migration_admin()=0 BEGIN SELECT RAISE(ABORT,'This project moved to its own database. Update and reopen the app.'); END")


def _session_columns(store):
    """Apply the existing Manager's optional history upgrades before it is started."""
    installed = ownership.tables(store.conn)
    if 'helper_sessions' not in installed:return
    with store.tx() as db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(helper_sessions)')}
        if 'task' not in columns:
            db.execute("ALTER TABLE helper_sessions ADD COLUMN task TEXT NOT NULL DEFAULT ''")
            if 'helpers' in installed:
                db.execute("UPDATE helper_sessions SET task=COALESCE((SELECT CASE WHEN json_valid(h.options) THEN "
                           "json_extract(h.options,'$.task') END FROM helpers h WHERE h.id=helper_sessions.helper_id "
                           "AND h.project=helper_sessions.project AND h.name=helper_sessions.agent),'')")
        if 'usage_coverage' not in columns:
            db.execute("ALTER TABLE helper_sessions ADD COLUMN usage_coverage TEXT NOT NULL DEFAULT 'unknown'")


def upgrade(net,log=print,*,fault=lambda phase:None):
    store = net.store
    if store.path==':memory:':raise ValueError('Automatic project storage requires a database file.')
    # One upgrade process owns admission throughout. No project workers exist yet;
    # all SQLite transactions remain local and separately committed.
    with store.writer_gate.hold(),store.lock:
        journal = state(store)
        if journal and journal['phase']=='ready':return journal
        store._migration_admin = True
        try:
            if journal is None:
                ownership.validate_schema(store.conn)
                log('Preparing automatic project database upgrade and recovery backup…')
                _session_columns(store)
                # Capture lifetime usage before transcript backfill evidence moves.
                from .app.tool_policy import ensure
                ensure(net)
                import shutil
                if shutil.disk_usage(Path(store.path).parent).free < 2*Path(store.path).stat().st_size:
                    raise ValueError('Free at least twice the database file size before automatic migration. The original database is unchanged.')
                backup = Path(store.path).with_name(Path(store.path).name+'.before-project-databases.sqlite3')
                temporary = Path(str(backup)+'.building')
                temporary.unlink(missing_ok=True)
                # SQLite admission also freezes raw/older writers that do not
                # honor WriterGate. A separate WAL reader backs up the committed
                # pre-fence state while this connection holds BEGIN IMMEDIATE.
                # Backing up this writer connection inside its transaction hangs.
                with store.tx():
                    _fence(store)
                    fault('snapshot-locked')
                    snapshot = sqlite3.connect(Path(store.path).resolve().as_uri()+'?mode=ro',uri=True)
                    destination = sqlite3.connect(temporary)
                    try:snapshot.backup(destination)
                    finally:
                        destination.close()  # Windows cannot rename an open SQLite file.
                        snapshot.close()
                    durable(temporary);os.replace(temporary,backup);os.chmod(backup,0o600);durable(backup)
                    projects = [dict(row) for row in store.all('SELECT * FROM projects ORDER BY id')]
                    for project in projects:bounds(project['id'])
                    journal = {'version':1,'phase':'copying','backup':str(backup.name),'projects':{},'copied':[],'checksums':{},'backup_checksum':_file_hash(backup)}
                    save(store,journal)
                fault('fenced')
            backup = Path(store.path).parent/journal['backup']
            if not backup.is_file():raise ValueError('The database migration backup is missing. Restore it before reopening the app.')
            if _file_hash(backup)!=journal['backup_checksum']:raise ValueError('The migration backup checksum changed. Restore the original upgrade backup.')
            source = sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)
            source.row_factory = sqlite3.Row
            try:
                ownership.validate_schema(source)
                selector = ownership.Settings(source)
                folder = Path(store.path).parent/'project-databases'
                folder.mkdir(exist_ok=True)
                for project in source.execute('SELECT * FROM projects ORDER BY id'):
                    pid = str(project['id'])
                    relative = 'project-databases/'+pid+'.sqlite3'
                    path = Path(store.path).parent/relative
                    if pid not in journal['copied']:
                        log('Migrating project database: '+project['slug'])
                        build(source,path,project,selector)
                        build(source,str(path)+'.transcripts.sqlite3',project,selector,transcripts=True)
                        fault('copied:'+pid)
                        journal['projects'][pid] = relative
                        journal['copied'].append(pid)
                        journal['checksums'][pid] = [_file_hash(path),_file_hash(str(path)+'.transcripts.sqlite3')]
                        save(store,journal)
                    elif not path.is_file() or not Path(str(path)+'.transcripts.sqlite3').is_file():
                        raise ValueError('A validated project database is missing. Restore it before resuming migration.')
                for pid,relative in journal['projects'].items():
                    path = Path(store.path).parent/relative
                    if [_file_hash(path),_file_hash(str(path)+'.transcripts.sqlite3')]!=journal['checksums'][pid]:
                        raise ValueError('A migrated project file changed before publication. Restore the migration backup.')
                fault('copies-ready')
                # Seed the global event delivery order while legacy event IDs are
                # still present. It commits independently before retirement.
                from .app.activity_index import bootstrap
                bootstrap(net)
                journal['phase'] = 'retiring'
                save(store,journal)
                fault('retiring')
                store.conn.execute('PRAGMA foreign_keys=OFF')
                try:
                    with store.tx() as db:
                        for table in sorted(ownership.tables(source).keys() & (ownership.SCOPED | ownership.TRANSCRIPTS | ownership.DERIVED | ownership.BLOBS)):
                            db.execute('DELETE FROM '+ownership.quote(table))
                        # Project settings have been copied to their exact owners.
                        db.executemany('DELETE FROM settings WHERE key=?',
                                       ((key,) for key,owner in selector.owners.items() if owner is not None))
                        if 'helper_sessions' in ownership.tables(source):
                            for name, in source.execute('SELECT DISTINCT agent FROM helper_sessions'):
                                db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',('helper-name:'+name,'1'))
                            floor = source.execute('SELECT COALESCE(MAX(helper_id),0) FROM helper_sessions').fetchone()[0]
                            if 'helpers' in ownership.tables(source):
                                floor = max(floor,source.execute('SELECT COALESCE(MAX(id),0) FROM helpers').fetchone()[0])
                            old = db.execute("SELECT value FROM settings WHERE key='helper-id-floor'").fetchone()
                            db.execute("INSERT OR REPLACE INTO settings VALUES('helper-id-floor',?)",(str(max(floor,int(old[0]) if old else 0)),))
                        if 'helpers' in ownership.tables(source) and 'helper_sessions' not in ownership.tables(source):
                            floor = source.execute('SELECT COALESCE(MAX(id),0) FROM helpers').fetchone()[0]
                            old = db.execute("SELECT value FROM settings WHERE key='helper-id-floor'").fetchone()
                            db.execute("INSERT OR REPLACE INTO settings VALUES('helper-id-floor',?)",(str(max(floor,int(old[0]) if old else 0)),))
                        maximum = source.execute('SELECT COALESCE(MAX(id),0) FROM projects').fetchone()[0]
                        db.execute("INSERT OR REPLACE INTO settings VALUES('project-id-floor',?)",(str(maximum),))
                        journal['phase'] = 'ready'
                        save(store,journal)
                    fault('published')
                finally:store.conn.execute('PRAGMA foreign_keys=ON')
                if store.one('PRAGMA foreign_key_check'):raise ValueError('The migrated catalog failed its foreign-key check; restore the upgrade backup.')
                return journal
            finally:source.close()
        finally:store._migration_admin = False
