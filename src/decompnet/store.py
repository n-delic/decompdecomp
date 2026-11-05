"""SQLite persistence. One file; writes through one connection and one lock, reads on a connection of each thread's
own. The write-ahead log (WAL) lets reads run beside a write: with everything on one connection and one lock, 16
agents on two games made every GUI request wait behind the others (the GUI's poll took 2.2-2.7 s instead of 14 ms)."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .read_cache import ReadCache

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
CREATE TABLE IF NOT EXISTS agent_selection(
  project TEXT NOT NULL, agent TEXT NOT NULL, policy TEXT NOT NULL, PRIMARY KEY(project, agent));
CREATE TABLE IF NOT EXISTS claims(
  function_id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL, claimed_at REAL, expires_at REAL);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY, function_id INTEGER, agent_id INTEGER, score INTEGER, compiled INTEGER,
  context_version INTEGER, source TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS attempt_hints(
  attempt_id INTEGER PRIMARY KEY, function_id INTEGER NOT NULL, summary TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_attempt_hints_fn ON attempt_hints(function_id,attempt_id);
CREATE TABLE IF NOT EXISTS agent_variant_runs(
  id INTEGER PRIMARY KEY, function_id INTEGER NOT NULL, family TEXT NOT NULL,
  revision TEXT NOT NULL, trials INTEGER NOT NULL, best_score INTEGER, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_agent_variant_fn ON agent_variant_runs(function_id,id);
CREATE TABLE IF NOT EXISTS pairings(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, secret_hash TEXT NOT NULL, token TEXT,
  project TEXT, state TEXT NOT NULL DEFAULT 'pending', agent_id INTEGER,
  created_at REAL NOT NULL, decided_at REAL);
CREATE TABLE IF NOT EXISTS agent_family_targets(
  function_id INTEGER NOT NULL, scope TEXT NOT NULL, family TEXT NOT NULL,
  target_hash TEXT NOT NULL, PRIMARY KEY(function_id,scope,family,target_hash));
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY, project_id INTEGER, function_id INTEGER, agent_id INTEGER, text TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, project_id INTEGER, agent_id INTEGER, kind TEXT, detail TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_functions_status ON functions(project_id, status, n_insns);
CREATE INDEX IF NOT EXISTS idx_attempts_fn ON attempts(function_id);
CREATE INDEX IF NOT EXISTS idx_attempts_worker ON attempts(function_id,agent_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_kind_id ON events(kind,id);
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
CREATE TABLE IF NOT EXISTS reuse_trials(
  function_id INTEGER NOT NULL, signature TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
  PRIMARY KEY(function_id, signature));
CREATE TABLE IF NOT EXISTS repair_cursors(project_id INTEGER PRIMARY KEY, function_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS iterative_runs(
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, attempts INTEGER NOT NULL, matched INTEGER NOT NULL,
    bytes INTEGER NOT NULL, seconds REAL NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS repair_frontiers(
    function_id INTEGER PRIMARY KEY REFERENCES functions(id) ON DELETE CASCADE, context TEXT NOT NULL, state TEXT NOT NULL);


CREATE TABLE IF NOT EXISTS repair_origins(
  function_id INTEGER NOT NULL, source_hash TEXT NOT NULL, source_function_id INTEGER NOT NULL,
  source_stamp TEXT NOT NULL, target_stamp TEXT NOT NULL, names TEXT NOT NULL,
  PRIMARY KEY(function_id,source_hash,source_function_id));
CREATE TABLE IF NOT EXISTS repaired_pairs(
  function_id INTEGER NOT NULL, source_function_id INTEGER NOT NULL, source_stamp TEXT NOT NULL,
  target_stamp TEXT NOT NULL, PRIMARY KEY(function_id,source_function_id));
CREATE TABLE IF NOT EXISTS reuse_cursors(project_id INTEGER PRIMARY KEY, function_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cross_game_index(
  function_id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, fingerprint TEXT NOT NULL,
  compiler TEXT NOT NULL, size INTEGER NOT NULL, matched_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cross_game_fingerprint ON cross_game_index(fingerprint, compiler);
CREATE TABLE IF NOT EXISTS cross_game_attempts(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, function_id INTEGER NOT NULL,
  source_project_id INTEGER NOT NULL, source_function_id INTEGER NOT NULL,
  outcome TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cross_game_attempts_project ON cross_game_attempts(project_id, created_at);
CREATE TABLE IF NOT EXISTS sdk_shapes(
 corpus TEXT NOT NULL, arch TEXT NOT NULL, big_endian INTEGER NOT NULL,
 name TEXT NOT NULL, fingerprint TEXT NOT NULL, size INTEGER NOT NULL, ops TEXT NOT NULL,
 PRIMARY KEY(corpus,arch,big_endian,name,fingerprint));
CREATE INDEX IF NOT EXISTS idx_sdk_shapes_size ON sdk_shapes(arch,big_endian,size);
CREATE TABLE IF NOT EXISTS sdk_sources(
  corpus TEXT NOT NULL, name TEXT NOT NULL, arch TEXT NOT NULL, big_endian INTEGER NOT NULL,
  source TEXT NOT NULL, header TEXT NOT NULL, compiler TEXT NOT NULL,
  PRIMARY KEY(corpus,name,arch,big_endian));
CREATE TABLE IF NOT EXISTS free_match_attempts(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, function_id INTEGER NOT NULL,
  strategy TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reuse_runs(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, attempts INTEGER NOT NULL, matched INTEGER NOT NULL,
  bytes INTEGER NOT NULL, seconds REAL NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_reuse_runs_project ON reuse_runs(project_id,id);
CREATE TABLE IF NOT EXISTS sdk_signatures(
  id INTEGER PRIMARY KEY, corpus TEXT NOT NULL, arch TEXT NOT NULL, big_endian INTEGER NOT NULL,
  name TEXT NOT NULL, fingerprint TEXT NOT NULL, size INTEGER NOT NULL,
  UNIQUE(corpus, arch, big_endian, name, fingerprint));
CREATE INDEX IF NOT EXISTS idx_sdk_signatures_fingerprint ON sdk_signatures(arch, big_endian, fingerprint);
CREATE TABLE IF NOT EXISTS compiler_tests(
  function_id INTEGER PRIMARY KEY, input_digest TEXT NOT NULL,
  completed_at REAL NOT NULL, result TEXT NOT NULL);
"""

# Columns added after the first release; applied in place to existing databases.
MIGRATIONS = [
    ("attempts", "score_before", "INTEGER"),
    ("attempts", "score_improved", "INTEGER"),
    ("notes", "session_id", "INTEGER"),
    ("notes", "score_before", "INTEGER"),
    ("notes", "score_after", "INTEGER"),
    ("reuse_trials", "feedback", "TEXT NOT NULL DEFAULT '{}'"),
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
    ("functions", "aside_mark", "TEXT"),                       # meta's set_aside (MARK_TRIGGERS: network.GAME_CODE)
    ("functions", "gone_mark", "TEXT"),                        # meta's gone (network.IN_BUILD)
]

# What the counts ask of each function's meta, copied into columns of their own: an index can hold a column, and
# answer the counts alone. An index on json_extract(meta, ...) does that from SQLite 3.50 (3.47 doesn't): older ones
# (Python 3.12.10's and before, a CI runner's) read every function's whole row, assembly and sources included. Not
# JSON (none is written so): no mark, as before a project's set-aside list.
MARKS = ("aside_mark=CASE WHEN json_valid(NEW.meta) THEN json_extract(NEW.meta, '$.set_aside') END, "
         "gone_mark=CASE WHEN json_valid(NEW.meta) THEN json_extract(NEW.meta, '$.gone') END")
MARK_TRIGGERS = f"""
CREATE TRIGGER IF NOT EXISTS functions_marks_insert AFTER INSERT ON functions WHEN NEW.meta IS NOT NULL
BEGIN UPDATE functions SET {MARKS} WHERE id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS functions_marks_update AFTER UPDATE OF meta ON functions
BEGIN UPDATE functions SET {MARKS} WHERE id=NEW.id; END;
"""

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


class _ThreadReader:
    """Close a worker's SQLite reader when thread-local storage is released.

    SQLite connections contain cycles; dropping the last thread-local reference
    alone can leave their WAL/database descriptors open until cyclic GC runs.
    """
    def __init__(self,path,header_codec):
        self.conn=sqlite3.connect(path,isolation_level=None,check_same_thread=False)
        try:
            self.conn.row_factory=sqlite3.Row
            header_codec.register(self.conn)
            self.conn.execute("PRAGMA query_only=1").close()
        except BaseException:
            self.conn.close()
            raise

    def __del__(self):
        # Only its owning thread reads this connection. Allow finalization from
        # another thread too, if the Store itself is collected after workers exit.
        try:self.conn.close()
        except Exception:pass


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        from .header_storage import Codec
        self.header_codec = Codec()
        self.header_codec.register(self.conn)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(f"PRAGMA journal_size_limit={WAL_SIZE_LIMIT_BYTES}")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        added = set()
        for table, column, ddl in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                added.add(column)
        from .header_storage import install as install_headers
        install_headers(self.conn)
        self.conn.executescript(DIRTY_TRIGGERS.replace("ON context_versions", "ON context_history").replace("project_id,header,unit", "project_id,header_hash,unit"))
        self.conn.executescript(MARK_TRIGGERS)
        if "aside_mark" in added:  # once: the marks of every function there is
            self.conn.execute(f"UPDATE functions SET {MARKS.replace('NEW.', '')} WHERE meta IS NOT NULL")
        # Project-local revisions invalidate cached contributor history in constant time, including
        # restores and score corrections that do not append a new attempt.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS history_revisions(project_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, rewritten INTEGER NOT NULL);
            CREATE TRIGGER IF NOT EXISTS history_attempt_insert AFTER INSERT ON attempts BEGIN
                INSERT INTO history_revisions SELECT project_id,1,(NEW.id < (SELECT MAX(id) FROM attempts))
                FROM functions WHERE id=NEW.function_id
                ON CONFLICT(project_id) DO UPDATE SET revision=revision+1,
                    rewritten=rewritten+(NEW.id < (SELECT MAX(id) FROM attempts));
            END;
            CREATE TRIGGER IF NOT EXISTS history_attempt_delete AFTER DELETE ON attempts BEGIN
                INSERT INTO history_revisions SELECT project_id,1,1 FROM functions WHERE id=OLD.function_id
                ON CONFLICT(project_id) DO UPDATE SET revision=revision+1,rewritten=rewritten+1;
            END;
            CREATE TRIGGER IF NOT EXISTS history_attempt_update AFTER UPDATE OF
                function_id,agent_id,score,compiled,score_improved,created_at ON attempts
                WHEN OLD.function_id IS NOT NEW.function_id OR OLD.agent_id IS NOT NEW.agent_id
                OR OLD.score IS NOT NEW.score OR OLD.compiled IS NOT NEW.compiled
                OR OLD.score_improved IS NOT NEW.score_improved OR OLD.created_at IS NOT NEW.created_at BEGIN
                INSERT INTO history_revisions SELECT project_id,1,1 FROM functions WHERE id IN (OLD.function_id,NEW.function_id)
                ON CONFLICT(project_id) DO UPDATE SET revision=revision+1,rewritten=rewritten+1;
            END;
            CREATE TRIGGER IF NOT EXISTS history_function_update AFTER UPDATE OF n_insns,project_id ON functions
                WHEN OLD.n_insns IS NOT NEW.n_insns OR OLD.project_id IS NOT NEW.project_id BEGIN
                INSERT INTO history_revisions VALUES(OLD.project_id,1,1)
                ON CONFLICT(project_id) DO UPDATE SET revision=revision+1,rewritten=rewritten+1;
                INSERT INTO history_revisions VALUES(NEW.project_id,1,1)
                ON CONFLICT(project_id) DO UPDATE SET revision=revision+1,rewritten=rewritten+1;
            END;
            CREATE TRIGGER IF NOT EXISTS history_function_insert AFTER INSERT ON functions BEGIN
                INSERT INTO history_revisions VALUES(NEW.project_id,1,1)
                ON CONFLICT(project_id) DO UPDATE SET revision=revision+1,rewritten=rewritten+1;
            END;
            CREATE TRIGGER IF NOT EXISTS history_function_delete AFTER DELETE ON functions BEGIN
                INSERT INTO history_revisions VALUES(OLD.project_id,1,1)
                ON CONFLICT(project_id) DO UPDATE SET revision=revision+1,rewritten=rewritten+1;
            END;
        """)
        # Map recency needs scores/timestamps, never saved C. Cover the window query so opening
        # a project does not read hundreds of MB of historical source from attempt table pages.
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_history ON "
                          "attempts(function_id,id,agent_id,compiled,score,score_improved,created_at)")
        self.conn.execute("DROP INDEX IF EXISTS idx_attempts_recency")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_agent_time ON attempts(agent_id, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_unit ON functions(project_id, unit, status)")
        # the room looks a function up by its address for every match, best attempt and note the host sends: without
        # this each lookup read every function's assembly and sources (89 ms for 18,800 functions; now 0.02)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_vram ON functions(project_id, vram)")
        # a note the host sends is saved only when this function doesn't have it already: that look-up read every
        # note saved so far (3 ms each at 7,000 notes, so 60,000 incoming notes took over three minutes; now 0.01)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_agent_time ON attempts(agent_id,created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_lookup ON notes(project_id, function_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_page ON notes(function_id,created_at DESC,id DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_session ON notes(function_id,agent_id,session_id)")
        # a function's events of one kind (polishing's tries on it: app/readable.py), without reading a game's whole feed
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_detail ON events(project_id, kind, detail)")
        # a project's last activity, for the project list and its page: read every one of its events (9 ms for ten
        # projects with 51,000 events, each sidebar ask; now 0.03)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project_time ON events(project_id, created_at)")
        # the progress the GUI asks for every second (totals, the map's "anything new?"), answered from this narrow
        # index instead of reading every function's assembly and sources: 31 ms -> about 2 for 36,000 functions
        # (with the set-aside and gone marks, whether it's polished and who matched it: the sidebar's and the project's
        # counts read every function's whole row without them, 58 ms for a 28,900-function game; now 7)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_tally ON functions(project_id, status, size, "
                          "attempts, best_score, matched_at, readable, matched_agent_id, aside_mark, gone_mark)")
        for old in ("idx_functions_progress", "idx_functions_counts"):  # (the second: this index on json_extract)
            self.conn.execute(f"DROP INDEX IF EXISTS {old}")
        # agents created before `origin` existed were all minted by the operator on the CLI
        self.conn.execute("UPDATE agents SET trusted = 1, origin = 'cli' WHERE origin IS NULL")
        # removing a project left its symbols behind before 0.10.3, and the next import (which may get the same id)
        # ran into them
        self.conn.execute("DELETE FROM symbols WHERE project_id NOT IN (SELECT id FROM projects)")
        self.conn.execute("DELETE FROM cross_game_index WHERE project_id NOT IN (SELECT id FROM projects)")
        self.conn.execute("DELETE FROM cross_game_attempts WHERE project_id NOT IN (SELECT id FROM projects) "
                          "OR source_project_id NOT IN (SELECT id FROM projects)")
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
        from .project_views import install as install_views
        install_views(self.conn)
        # what the last run left in the log goes into the database now, and the log starts empty
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS read_revisions(kind TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
        self.read_cache = ReadCache(lambda: getattr(getattr(self, '_local', None), 'in_tx', False))
        for table, columns in (
                ('functions', 'target,relocs,asm,seed,name,unit,vram,n_insns,size,status,best_source,best_score,matched_source,meta,project_id,gone_mark,aside_mark'),
                ('projects', 'config,arch,big_endian,root'), ('symbols', None), ('context_versions', None)):
            self._track_reads(table, 'inputs', columns)
        self._track_reads('functions', 'targets', 'target,status,project_id,gone_mark,aside_mark',
            "OLD.target IS NOT NEW.target OR OLD.project_id IS NOT NEW.project_id OR "
            "OLD.gone_mark IS NOT NEW.gone_mark OR OLD.aside_mark IS NOT NEW.aside_mark OR "
            "(OLD.status='asm') != (NEW.status='asm')")
        self._track_reads('projects', 'targets', 'arch,big_endian')
        self._track_reads('functions', 'compiler_rows', 'name,vram,size,n_insns,status,project_id')
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_deep_ready ON "
                          "functions(project_id,status,gone_mark,(best_source IS NOT NULL),best_score)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_compiler_rows ON "
                          "functions(project_id,vram,id,name,size,n_insns,status)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS read_snapshots(key TEXT PRIMARY KEY, version TEXT NOT NULL, value TEXT NOT NULL)")
        self.conn.execute('SAVEPOINT fingerprint_trigger_upgrade')
        try:
            # Replace the older broad config/preview triggers when upgrading an existing database.
            for table in ('functions', 'projects', 'symbols'):
                for event in ('insert', 'delete', 'update'):
                    self.conn.execute(f'DROP TRIGGER IF EXISTS read_fingerprints_{table}_{event}')
            self._track_reads('functions', 'fingerprints', 'target,relocs,status,project_id',
                "OLD.target IS NOT NEW.target OR OLD.relocs IS NOT NEW.relocs OR OLD.project_id IS NOT NEW.project_id OR "
                "(OLD.status='asm') != (NEW.status='asm')")
            self._track_reads('projects', 'fingerprints', 'arch,big_endian,config',
                "OLD.arch IS NOT NEW.arch OR OLD.big_endian IS NOT NEW.big_endian OR "
                "json_extract(OLD.config,'$.big_endian') IS NOT json_extract(NEW.config,'$.big_endian') OR "
                "json_extract(OLD.config,'$.kind') IS NOT json_extract(NEW.config,'$.kind')")
            self._track_reads('symbols', 'fingerprints', 'name,address,size,kind,project_id')
        except BaseException:
            self.conn.execute('ROLLBACK TO fingerprint_trigger_upgrade')
            self.conn.execute('RELEASE fingerprint_trigger_upgrade')
            raise
        else:
            self.conn.execute('RELEASE fingerprint_trigger_upgrade')
        self._track_reads('symbols', 'scope_symbols', 'name,address,size,kind,project_id')
        self._track_reads('context_versions', 'scope_context')
        self._track_reads('cross_game_index', 'reuse_index')
        self._track_reads('functions', 'matched_evidence', 'name,status,matched_source,project_id')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_cross_game_compiler_size ON cross_game_index(compiler,size,project_id,fingerprint,function_id)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_functions_matched_id ON functions(status,id)')
        # SDK inventory is global, unlike the project-owned tables tracked above.
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS read_sdk_inventory_{event} AFTER {event} ON sdk_signatures "
                              "BEGIN INSERT INTO read_revisions VALUES('sdk_names',1) "
                              "ON CONFLICT(kind) DO UPDATE SET revision=revision+1; END")
        self._track_reads('projects', 'project_list', 'slug,name,root,config,arch,big_endian')
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_baseline ON attempts(function_id,score,id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_context ON attempts(context_version,function_id)")
        from .history_changes import install as install_history_changes
        install_history_changes(self.conn)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_history ON functions(project_id,id,n_insns)")
        self._track_reads('functions', 'naming', 'name,meta,project_id')
        from .map_changes import install as install_map_changes
        install_map_changes(self.conn)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_context_latest ON context_history(project_id,unit,version DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_named_scan ON functions(project_id,status,id,name)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_functions_room_credit ON functions(project_id,matched_agent_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_origin_owner ON agents(origin,owner)")
        self._track_reads('functions', 'audit_layout', 'name,vram,rom_offset,size,project_id')
        self._slow_queries = {}
        self.lock = threading.RLock()
        self._local = threading.local()  # per thread: its reading connection, and whether it's inside tx()

    def _track_reads(self, table, kind, columns=None, condition=None):
        """Internal schema names only. Revisions commit/roll back with their source changes."""
        if table == "context_versions":
            table = "context_history"
        for event in ('INSERT', 'DELETE', 'UPDATE'):
            clause = f'UPDATE OF {columns}' if event == 'UPDATE' and columns else event
            if event == 'UPDATE' and columns and condition is None:
                condition_sql = ' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in columns.split(','))
            else:
                condition_sql = condition if event == 'UPDATE' else None
            when = f'WHEN {condition_sql}' if condition_sql else ''
            records = ('NEW', 'OLD') if event == 'UPDATE' else (('NEW',) if event == 'INSERT' else ('OLD',))
            def project(record):
                if table == 'projects':
                    return f'{record}.id'
                if table == 'deep_permutation_passes':
                    return f'(SELECT project_id FROM functions WHERE id={record}.function_id)'
                return f'{record}.project_id'
            affected = ' UNION '.join(f'SELECT {project(r)} AS pid' for r in records)
            self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS read_{kind}_{table}_{event.lower()} AFTER {clause} ON {table} {when} "
                f"BEGIN INSERT INTO read_revisions VALUES('{kind}',1) "
                "ON CONFLICT(kind) DO UPDATE SET revision=revision+1; "
                f"INSERT INTO read_revisions SELECT '{kind}:' || pid,1 FROM ({affected}) WHERE pid IS NOT NULL "
                "ON CONFLICT(kind) DO UPDATE SET revision=revision+1; END")

    def read_version(self, kind, project=None):
        key = kind if project is None else f'{kind}:{project}'
        row = self.one('SELECT revision FROM read_revisions WHERE kind=?', key)
        return row['revision'] if row else 0

    @contextmanager
    def tx(self):
        """Serialized transaction. Never compile while holding this. One inside another joins it: they commit (or
        roll back) together, which is how many small writes cost one commit's wait for the disk."""
        from .request_timing import timed_lock
        callbacks = []
        with timed_lock(self.lock,'write_lock_wait'):
            if getattr(self._local, "in_tx", False):
                yield self.conn
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._local.in_tx = True
            self._local.after_commit = []
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                self._local.after_commit = []
                raise
            finally:
                self._local.in_tx = False
            self.conn.execute("COMMIT")
            callbacks, self._local.after_commit = self._local.after_commit, []
        for callback in callbacks:
            callback()

    def after_commit(self, callback):
        """Publish external derived evidence only after its enclosing write succeeds."""
        if getattr(self._local, 'in_tx', False):
            self._local.after_commit.append(callback)
        else:
            callback()

    def _reader(self) -> sqlite3.Connection | None:
        """This thread's own connection for reading, or None: inside tx() (a read there must see what the transaction
        wrote) and for an in-memory database, reads go through the writer's connection."""
        if self.path == IN_MEMORY or getattr(self._local, "in_tx", False):
            return None
        reader = getattr(self._local, "reader", None)
        if reader is None:
            reader = _ThreadReader(self.path,self.header_codec)
            self._local.reader = reader
        return reader.conn

    def _read(self, sql: str, args: tuple, single: bool):
        started = time.monotonic()
        self._local.read_phases = {}
        try:
            return self._read_rows(sql, args, single)
        finally:
            elapsed = time.monotonic() - started
            from .request_timing import sql_read
            phases = self._local.read_phases
            sql_read(elapsed, phases)
            if elapsed >= 0.25:
                # Never log bindings or SQL text: either can contain private source or credentials.
                key = hashlib.sha256(sql.encode()).hexdigest()[:12]
                if started - self._slow_queries.get(key, float('-inf')) >= 60:
                    if len(self._slow_queries) >= 128:
                        self._slow_queries.clear()
                    self._slow_queries[key] = started
                    caller = sys._getframe(2)
                    logging.getLogger('decompdecomp').warning(
                        'Slow database read %.3fs [%s] at %s:%s (%s) connection=%.3fs lock_wait=%.3fs execute=%.3fs fetch=%.3fs', elapsed, key,
                        Path(caller.f_code.co_filename).name, caller.f_lineno, caller.f_code.co_name,
                        *(phases.get(k, 0) for k in ('connection', 'lock_wait', 'execute', 'fetch')))

    def _read_rows(self, sql: str, args: tuple, single: bool):
        started = time.perf_counter()
        phases = self._local.read_phases
        try:
            conn = self._reader()
        finally:
            phases['connection'] = time.perf_counter() - started
        def query(connection):
            executing = time.perf_counter()
            try:
                cur = connection.execute(sql, args)
            finally:
                # Failed reads can spend the entire SQLite busy timeout here.
                phases['execute'] = time.perf_counter() - executing
            fetching = time.perf_counter()
            try:
                return cur.fetchone() if single else cur.fetchall()
            finally:
                cur.close()
                phases['fetch'] = time.perf_counter() - fetching
        if conn is None:
            waiting = time.perf_counter()
            with self.lock:
                phases['lock_wait'] = time.perf_counter() - waiting
                return query(self.conn)
        return query(conn)

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        return self._read(sql, args, single=True)

    def all(self, sql: str, *args) -> list[sqlite3.Row]:
        return self._read(sql, args, single=False)
