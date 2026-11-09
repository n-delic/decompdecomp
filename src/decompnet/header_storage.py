"""Exact C headers stored once as compressed blobs, with the historical SQL interface intact.

Version metadata stays separate from content. The view keeps all existing judging, export and
repair readers on the same bytes; SQLite transactions also cover its write-through triggers.
"""
import hashlib
import threading
import zlib
from functools import lru_cache


class Codec:
    def __init__(self):
        self.pins = set()
        self.lock = threading.RLock()

    @staticmethod
    def digest(text):
        return hashlib.sha256(text.encode('utf-8')).hexdigest() if text is not None else None

    @staticmethod
    def pack(text):
        return zlib.compress(text.encode('utf-8')) if text is not None else None

    @staticmethod
    @lru_cache(maxsize=16)
    def unpack(digest, payload):
        text = zlib.decompress(payload).decode('utf-8')
        if Codec.digest(text) != digest:
            raise ValueError('Stored C header checksum failed; restore the database from a backup.')
        return text

    def read(self, project, version, digest, payload):
        with self.lock:
            self.pins.add((project, version))  # An in-flight compile may still need this version.
        if digest is None:
            return None
        if payload is None:
            raise ValueError('Stored C header content is missing; restore the database from a backup.')
        return self.unpack(digest, payload)

    def register(self, conn):
        from .payload_storage import register
        register(conn)
        conn.create_function('header_digest', 1, self.digest, deterministic=True)
        conn.create_function('header_pack', 1, self.pack, deterministic=True)
        # Reading also pins the version, so this function must not be marked deterministic.
        conn.create_function('header_read', 4, self.read)


def install(conn):
    """Atomically migrate legacy text rows, preserving version keys, metadata and exact text."""
    if conn.execute("SELECT type FROM sqlite_master WHERE name='context_versions'").fetchone()[0] == 'view':
        return
    conn.execute('SAVEPOINT compressed_headers')
    try:
        conn.execute('CREATE TABLE header_blobs(hash TEXT PRIMARY KEY, payload BLOB NOT NULL, raw_bytes INTEGER NOT NULL)')
        conn.execute('CREATE TABLE context_history(project_id INTEGER, version INTEGER, header_hash TEXT REFERENCES header_blobs(hash), '
                     "agent_id INTEGER, message TEXT, created_at REAL, unit TEXT NOT NULL DEFAULT '', PRIMARY KEY(project_id,version))")
        # Stream one header at a time rather than loading the entire history into Python memory.
        for row in conn.execute('SELECT project_id,version,header,agent_id,message,created_at,unit FROM context_versions'):
            project, version, header, agent, message, created, unit = row
            digest = Codec.digest(header)
            if header is not None and not conn.execute('SELECT 1 FROM header_blobs WHERE hash=?', (digest,)).fetchone():
                conn.execute('INSERT INTO header_blobs VALUES(?,?,?)',
                             (digest, Codec.pack(header), len(header.encode('utf-8'))))
            conn.execute('INSERT INTO context_history VALUES(?,?,?,?,?,?,?)',
                         (project, version, digest, agent, message, created, unit))
        conn.execute('DROP TABLE context_versions')
        conn.execute('CREATE VIEW context_versions AS SELECT project_id,version,'
                     'header_read(project_id,version,header_hash,(SELECT payload FROM header_blobs WHERE hash=header_hash)) AS header,'
                     'agent_id,message,created_at,unit FROM context_history')
        blob = "INSERT OR IGNORE INTO header_blobs SELECT header_digest(NEW.header),header_pack(NEW.header),length(CAST(NEW.header AS BLOB)) WHERE NEW.header IS NOT NULL AND NOT EXISTS(SELECT 1 FROM header_blobs WHERE hash=header_digest(NEW.header));"
        conn.execute('CREATE TRIGGER context_versions_insert INSTEAD OF INSERT ON context_versions BEGIN ' + blob +
                     'INSERT INTO context_history VALUES(NEW.project_id,NEW.version,header_digest(NEW.header),NEW.agent_id,NEW.message,NEW.created_at,COALESCE(NEW.unit,\'\')); END')
        conn.execute('CREATE TRIGGER context_versions_update INSTEAD OF UPDATE ON context_versions BEGIN ' + blob +
                     'UPDATE context_history SET project_id=NEW.project_id,version=NEW.version,header_hash=header_digest(NEW.header),'
                     'agent_id=NEW.agent_id,message=NEW.message,created_at=NEW.created_at,unit=NEW.unit '
                     'WHERE project_id=OLD.project_id AND version=OLD.version; END')
        conn.execute('CREATE TRIGGER context_versions_delete INSTEAD OF DELETE ON context_versions BEGIN '
                     'DELETE FROM context_history WHERE project_id=OLD.project_id AND version=OLD.version; END')
        conn.execute('CREATE INDEX idx_context_latest ON context_history(project_id,unit,version DESC)')
        conn.execute('CREATE INDEX idx_context_hash ON context_history(header_hash)')
    except BaseException:
        conn.execute('ROLLBACK TO compressed_headers')
        raise
    finally:
        conn.execute('RELEASE compressed_headers')


def stats(store):
    row = store.one('SELECT COUNT(*) versions,COALESCE(SUM(b.raw_bytes),0) logical_bytes '
                    'FROM context_history h LEFT JOIN header_blobs b ON b.hash=h.header_hash')
    blobs = store.one('SELECT COUNT(*) contents,COALESCE(SUM(length(payload)),0) stored_bytes FROM header_blobs')
    return {**dict(row), **dict(blobs)}
