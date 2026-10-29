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
CREATE TABLE IF NOT EXISTS progress_state(
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  disk TEXT NOT NULL, projected TEXT NOT NULL, updated_at REAL NOT NULL);
-- What changed in a project since its progress folder was last published (progress.py). Triggers fill these instead
-- of every write site remembering to: a claim, an attempt that didn't improve the best, or an event must not make
-- the folder re-render, while a match, a header, a note or a layout change must. `token` is new on every relevant
-- write, so a write that lands while a sync is rendering has a token the sync did not acknowledge.
CREATE TABLE IF NOT EXISTS progress_dirty(
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  token TEXT,  -- a new random token per relevant write: unlike a counter, a rollback cannot reuse it
  functions INTEGER NOT NULL DEFAULT 0,
  headers INTEGER NOT NULL DEFAULT 0,
  metadata INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS progress_dirty_file(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, token TEXT,
  PRIMARY KEY(project_id, name));
-- The last published folder and database render, one hash per file: a sync compares hashes and writes only the rows
-- whose file changed, instead of serialising two copies of every file (23 MB at 18,000 functions, every sync).
CREATE TABLE IF NOT EXISTS progress_files(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  side TEXT NOT NULL, name TEXT NOT NULL, hash TEXT NOT NULL,
  PRIMARY KEY(project_id, side, name));
-- a room guest's saved work waiting for the host (app/collab.py): the full sources live here, not in projects.config,
-- which every project access parses
CREATE TABLE IF NOT EXISTS saved_progress(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  room TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, score INTEGER,
  vram INTEGER, size INTEGER, best_comparable INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(project_id, name));
CREATE TABLE IF NOT EXISTS symbols(
  project_id INTEGER NOT NULL, name TEXT NOT NULL, address INTEGER NOT NULL, size INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'data', preview TEXT, PRIMARY KEY(project_id, name));
CREATE INDEX IF NOT EXISTS idx_symbols_addr ON symbols(project_id, address);
-- a function an agent found needs another one done first (release_function's `needs`): claimed last until that one
-- matches, which is claimed first meanwhile; then it comes first once
CREATE TABLE IF NOT EXISTS waits(
  function_id INTEGER PRIMARY KEY, needs_id INTEGER NOT NULL, agent_id INTEGER, created_at REAL);
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
    ("progress_state", "format", "INTEGER NOT NULL DEFAULT 1"),  # 2: disk/projected are hashes in progress_files
    ("progress_state", "disk_manifest", "TEXT"),               # the last acknowledged folder manifest (three-way merges)
    ("progress_state", "projected_manifest", "TEXT"),          # the database manifest it was published with
    ("progress_state", "config_key", "TEXT"),                  # the settings the projected manifest was rendered from
    ("progress_state", "dirty_token", "TEXT"),  # the progress_dirty token the last publish acknowledged
]

# Created after the migrations above: they name columns older databases get from MIGRATIONS. A trigger only marks what
# a progress folder's render actually reads (saves._render), so work that stays in the database costs nothing.
DIRTY_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS progress_dirty_functions_insert AFTER INSERT ON functions
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), functions=1 WHERE project_id=NEW.project_id;
  INSERT OR REPLACE INTO progress_dirty_file(project_id,name,token)
    SELECT NEW.project_id, NEW.name, token FROM progress_dirty WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_functions_update
AFTER UPDATE OF name,vram,size,status,matched_source,matched_agent_id,best_source,best_score,best_agent_id,readable,meta
ON functions
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), functions=1 WHERE project_id=NEW.project_id;
  INSERT OR REPLACE INTO progress_dirty_file(project_id,name,token)
    SELECT OLD.project_id, OLD.name, token FROM progress_dirty WHERE project_id=OLD.project_id;
  INSERT OR REPLACE INTO progress_dirty_file(project_id,name,token)
    SELECT NEW.project_id, NEW.name, token FROM progress_dirty WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_functions_delete AFTER DELETE ON functions
WHEN EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(OLD.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), functions=1 WHERE project_id=OLD.project_id;
  INSERT OR REPLACE INTO progress_dirty_file(project_id,name,token)
    SELECT OLD.project_id, OLD.name, token FROM progress_dirty WHERE project_id=OLD.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_notes_insert AFTER INSERT ON notes
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_notes_update AFTER UPDATE OF project_id,function_id,agent_id,text ON notes
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_notes_delete AFTER DELETE ON notes
WHEN EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(OLD.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=OLD.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_headers_insert AFTER INSERT ON context_versions
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), headers=1 WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_headers_update AFTER UPDATE OF project_id,header,unit ON context_versions
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), headers=1 WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_headers_delete AFTER DELETE ON context_versions
WHEN EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(OLD.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), headers=1 WHERE project_id=OLD.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_symbols_insert AFTER INSERT ON symbols
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_symbols_update AFTER UPDATE OF project_id,name,address,size,kind ON symbols
WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_symbols_delete AFTER DELETE ON symbols
WHEN EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(OLD.project_id);
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=OLD.project_id;
END;
CREATE TRIGGER IF NOT EXISTS progress_dirty_agent_rename AFTER UPDATE OF name ON agents
WHEN EXISTS (SELECT 1 FROM projects WHERE CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
BEGIN
  INSERT OR IGNORE INTO progress_dirty(project_id)
    SELECT id FROM projects WHERE CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom';
  UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1
    WHERE project_id IN (SELECT id FROM projects WHERE CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom');
END;
"""


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
        self.conn.executescript(DIRTY_TRIGGERS)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_agent_time ON attempts(agent_id, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_unit ON functions(project_id, unit, status)")
        # the room looks a function up by its address for every match, best attempt and note the host sends: without
        # this each lookup read every function's assembly and sources (89 ms for 18,800 functions; now 0.02)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_vram ON functions(project_id, vram)")
        # a note the host sends is saved only when this function doesn't have it already: that look-up read every
        # note saved so far (3 ms each at 7,000 notes, so 60,000 incoming notes took over three minutes; now 0.01)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_lookup ON notes(project_id, function_id)")
        # a function's events of one kind (polishing's tries on it: app/readable.py), without reading a game's whole feed
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_detail ON events(project_id, kind, detail)")
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
        """Serialized transaction. Never compile while holding this. One inside another joins it: they commit (or
        roll back) together, which is how many small writes cost one commit's wait for the disk."""
        with self.lock:
            if getattr(self._local, "in_tx", False):
                yield self.conn
                return
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
