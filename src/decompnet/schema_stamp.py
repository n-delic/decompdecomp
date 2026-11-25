"""Run a database's schema setup once per version of the code that writes it, not once per object that needs it.

Every `RepairFeedback` and every `repair_outcomes.prepare` used to open a write transaction that re-ran its DDL
(`DROP VIEW` / `CREATE VIEW`, `CREATE TABLE IF NOT EXISTS`, triggers). On a running Deep Permuter that was 700 such
transactions in half an hour, each holding the project's single writer for most of a second while using ~10 ms of
CPU, and every DDL commit also made each other connection reparse the schema. A stamp in `settings` records that
the setup already ran for this exact code, so a later start only reads it. The stamp is the SHA-256 of the files
that define the schema, so editing them re-runs the setup on the next start with no version to remember to bump.
"""
import functools
import hashlib
from pathlib import Path

PREFIX = 'schema-stamp:'


@functools.lru_cache(maxsize=None)
def digest(*modules):
    """The SHA-256 of the named modules' source files (names relative to the decompnet package)."""
    root = Path(__file__).parent
    sha = hashlib.sha256()
    for name in modules:
        sha.update(name.encode() + b'\0' + (root / name).read_bytes() + b'\0')
    return sha.hexdigest()


def current(store, name, *modules):
    """Whether `name`'s setup already ran for the code in `modules`."""
    row = store.one('SELECT value FROM settings WHERE key=?', PREFIX + name)
    return row is not None and row['value'] == digest(*modules)


def stamp(db, name, *modules):
    """Record, inside the setup's own transaction `db`, that it ran for the code in `modules`."""
    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (PREFIX + name, digest(*modules)))
