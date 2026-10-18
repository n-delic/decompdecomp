"""SQLite persistence. One file; writes through one connection and one lock, reads on a connection of each thread's
own. The write-ahead log (WAL) lets reads run beside a write: with everything on one connection and one lock, 16
agents on two games made every GUI request wait behind the others (the GUI's poll took 2.2-2.7 s instead of 14 ms)."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, owner TEXT, token_hash TEXT UNIQUE NOT NULL,
  created_at REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT, description TEXT, arch TEXT,
  big_endian INTEGER NOT NULL DEFAULT 0, root TEXT NOT NULL, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS context_versions(
  project_id INTEGER, version INTEGER, header TEXT, agent_id INTEGER, message TEXT, created_at REAL,
  PRIMARY KEY(project_id, version));
CREATE TABLE IF NOT EXISTS functions(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, name TEXT NOT NULL, size INTEGER, n_insns INTEGER,
  target BLOB, relocs TEXT, asm TEXT, seed TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  best_score INTEGER, best_source TEXT, best_agent_id INTEGER,
  matched_source TEXT, matched_agent_id INTEGER, matched_at REAL,
  attempts INTEGER NOT NULL DEFAULT 0, abandons INTEGER NOT NULL DEFAULT 0,
  UNIQUE(project_id, name));
CREATE TABLE IF NOT EXISTS claims(
  function_id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL, claimed_at REAL, expires_at REAL);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY, function_id INTEGER, agent_id INTEGER, score INTEGER, compiled INTEGER,
  context_version INTEGER, source TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY, project_id INTEGER, function_id INTEGER, agent_id INTEGER, text TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, project_id INTEGER, agent_id INTEGER, kind TEXT, detail TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_functions_status ON functions(project_id, status, n_insns);
CREATE INDEX IF NOT EXISTS idx_attempts_fn ON attempts(function_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS symbols(
  project_id INTEGER NOT NULL, name TEXT NOT NULL, address INTEGER NOT NULL, size INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'data', preview TEXT, PRIMARY KEY(project_id, name));
CREATE INDEX IF NOT EXISTS idx_symbols_addr ON symbols(project_id, address);
CREATE TABLE IF NOT EXISTS twin_misses(
  function_id INTEGER NOT NULL, source_hash TEXT NOT NULL, project_id INTEGER, at REAL, why TEXT,
  PRIMARY KEY(function_id, source_hash));
"""

# Columns added after the first release; applied in place to existing databases.
MIGRATIONS = [
    ("agents", "trusted", "INTEGER NOT NULL DEFAULT 0"),      # operator-vetted (CLI) vs. self-registered
    ("agents", "origin", "TEXT"),                              # cli | web
    ("agents", "terms_accepted_at", "REAL"),                   # evidence of accepting the terms
    ("functions", "fingerprint", "TEXT"),                      # one-way match fingerprint (twins, regression checks)
    ("attempts", "via", "TEXT"),                               # agent | permuter
    ("functions", "unit", "TEXT NOT NULL DEFAULT ''"),         # source file (translation unit) it came from
    ("context_versions", "unit", "TEXT NOT NULL DEFAULT ''"),  # '' = project-wide header, else per-unit header
    ("functions", "vram", "INTEGER"),                          # ROM projects: load address of the function
    ("functions", "rom_offset", "INTEGER"),                    # ROM projects: where its bytes are in the ROM
    ("functions", "meta", "TEXT"),                             # forks: symbol, demangled name, source file
    ("functions", "boost", "INTEGER NOT NULL DEFAULT 0"),      # claim order: >0 matching it matches that many twins
    ("functions", "readable", "INTEGER NOT NULL DEFAULT 0"),   # matched C rewritten readable, still matching (readable.py)
    ("functions", "permuted", "INTEGER NOT NULL DEFAULT 0"),   # turns the permuter spent on it (app/permute.py)
    ("functions", "seed_key", "TEXT"),                         # what its machine draft was made from (engine/briefs.py)
]


# The write-ahead log is cut back to this after a checkpoint. Without a limit it never shrinks: the owner's grew to
# 90 MB beside a 172 MB database, and every start read through it.
WAL_SIZE_LIMIT_BYTES = 64 * 1024 * 1024
IN_MEMORY = ":memory:"  # a database only its own connection sees: reads go through the writer's connection


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(f"PRAGMA journal_size_limit={WAL_SIZE_LIMIT_BYTES}")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_agent_time ON attempts(agent_id, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_unit ON functions(project_id, unit, status)")
        # the progress the GUI asks for every second (totals, the map's "anything new?"), answered from this narrow
        # index instead of reading every function's assembly and sources: 31 ms -> about 2 for 36,000 functions
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_progress ON functions(project_id, status, size, "
                          "attempts, best_score, matched_at)")
        # agents created before `origin` existed were all minted by the operator on the CLI
        self.conn.execute("UPDATE agents SET trusted = 1, origin = 'cli' WHERE origin IS NULL")
        # removing a project left its symbols behind before 0.10.3, and the next import (which may get the same id)
        # ran into them
        self.conn.execute("DELETE FROM symbols WHERE project_id NOT IN (SELECT id FROM projects)")
        # before 0.10.4 a twin's try left the function claimed by the autopilot for 30 minutes, and each expiry
        # counted as an agent giving up on it: thousands of functions were put last for nothing. Counted again,
        # once, from what agents actually did (releases, and their own claims that ran out)
        if self.conn.execute("SELECT 1 FROM settings WHERE key='abandons_recounted'").fetchone() is None:
            self.conn.executescript("""
                BEGIN;
                CREATE TEMP TABLE gave_up AS SELECT project_id, detail AS name, COUNT(*) AS n FROM events
                  WHERE kind='release' OR (kind='expired' AND COALESCE(agent_id, -1) NOT IN
                    (SELECT id FROM agents WHERE name IN ('autopilot', 'permuter')))
                  GROUP BY project_id, detail;
                CREATE INDEX temp.gave_up_name ON gave_up(project_id, name);
                UPDATE functions SET abandons = COALESCE((SELECT n FROM gave_up g WHERE g.project_id=functions.project_id
                                                          AND g.name=functions.name), 0) WHERE abandons > 0;
                DROP TABLE gave_up;
                INSERT INTO settings(key, value) VALUES('abandons_recounted', '1');
                COMMIT;""")
        # what the last run left in the log goes into the database now, and the log starts empty
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.lock = threading.RLock()
        self._local = threading.local()  # per thread: its reading connection, and whether it's inside tx()

    @contextmanager
    def tx(self):
        """Serialized transaction. Never compile while holding this."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            self._local.in_tx = True
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            finally:
                self._local.in_tx = False
            self.conn.execute("COMMIT")

    def _reader(self) -> sqlite3.Connection | None:
        """This thread's own connection for reading, or None: inside tx() (a read there must see what the transaction
        wrote) and for an in-memory database, reads go through the writer's connection."""
        if self.path == IN_MEMORY or getattr(self._local, "in_tx", False):
            return None
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=1")  # a write here would bypass the writer's lock
            self._local.conn = conn
        return conn

    def _read(self, sql: str, args: tuple, single: bool):
        conn = self._reader()
        if conn is None:
            with self.lock:
                cur = self.conn.execute(sql, args)
                return cur.fetchone() if single else cur.fetchall()
        cur = conn.execute(sql, args)
        try:
            return cur.fetchone() if single else cur.fetchall()
        finally:
            cur.close()  # an open statement would hold its snapshot, and the log could never be checkpointed

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        return self._read(sql, args, single=True)

    def all(self, sql: str, *args) -> list[sqlite3.Row]:
        return self._read(sql, args, single=False)
