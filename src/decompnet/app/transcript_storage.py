"""Small WAL database for one project's conversation payloads, independent of saved work."""
import sqlite3
import threading

from ..header_storage import Codec
from ..payload_storage import Connection
from ..read_cache import ReadCache
from ..store import WAL_SIZE_LIMIT_BYTES, Store


class TranscriptStore(Store):
    def _initialize(self):
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, factory=Connection)
        self.conn.row_factory = sqlite3.Row
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
