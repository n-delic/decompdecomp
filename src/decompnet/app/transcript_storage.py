"""Small WAL database for one project's conversation payloads, independent of saved work."""
import sqlite3
import threading
from pathlib import Path

from ..header_storage import Codec
from ..payload_storage import Connection
from ..read_cache import ReadCache
from ..store import WAL_SIZE_LIMIT_BYTES, Store


class TranscriptStore(Store):
    def _initialize(self):
        location = Path(self.path).resolve().as_uri()+'?mode=rw' if self.require_existing else self.path
        self.conn = sqlite3.connect(location, uri=self.require_existing, check_same_thread=False, isolation_level=None, factory=Connection)
        self.conn.row_factory = sqlite3.Row
        if self.require_existing:
            try:marker = self.conn.execute("SELECT value FROM settings WHERE key='transcript-project-id'").fetchone()
            except sqlite3.DatabaseError as error:raise ValueError('The transcript database is incomplete. Restore a consistent backup.') from error
            if not marker or marker[0]!=str(self.project_namespace):raise ValueError('The transcript database belongs to a different project or is incomplete. Restore a consistent backup.')
        if self.repair_archive is not None:self.conn.repair_archive = self.repair_archive
        self.header_codec = Codec()
        self.header_codec.register(self.conn)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute(f'PRAGMA journal_size_limit={WAL_SIZE_LIMIT_BYTES}')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS read_revisions(kind TEXT PRIMARY KEY,revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS text_payloads(hash TEXT PRIMARY KEY,payload BLOB NOT NULL,raw_bytes INTEGER NOT NULL);
        ''')
        from ..payload_sizes import install
        install(self.conn)
        self.lock = threading.RLock()
        self._local = threading.local()
        self._slow_queries = {}
        self.read_cache = ReadCache(lambda: getattr(self._local,'in_tx',False))
