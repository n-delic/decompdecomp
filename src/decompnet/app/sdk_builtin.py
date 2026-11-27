"""Library signatures the app ships for a console, loaded into the shared SDK index as a corpus of their own.

Library detection compares a game's functions with signatures of library code (sdk_identity.identify): full-size,
relocation-normalised fingerprints, never a familiar name. Those came only from SDK folders the user indexed, so a
user without the SDK got next to nothing: a PC with no N64 SDK found 2 library functions in a racing game where one
with six SDK folders found 152. A console whose library has been rebuilt byte for byte from public source ships a
table of that rebuild's fingerprints (`Console.sdk_table`: the N64's from decompals/ultralib, made by
tools/ultralib_signatures.py): hashes, sizes, names and the builds they came from, no code. It's loaded here into
sdk_signatures like an indexed archive, so detection, naming, a game's library choice (Settings) and the port read it
the same way; the user's own SDK folders add to it, and scanning them never removes it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from .. import consoles

KEY = "sdk-builtin:"  # settings key per console: the table's digest and corpus, so a start reloads only a new table


def _parse(path: Path, data: bytes) -> tuple[str, list[tuple[str, int, str]]]:
    """(corpus, [(fingerprint, size, name)]) of a table: a first comment line naming its source and commit, then
    tab-separated fingerprint, size, name and builds."""
    lines = data.decode("utf-8").splitlines()
    source = lines[0].lstrip("# ").split(",")[0].split()
    corpus = f"built in / {source[0]} {source[1][:12]}" if len(source) > 1 else f"built in / {path.stem}"
    rows = []
    for line in lines:
        if line and not line.startswith("#"):
            fingerprint, size, name, *_ = line.split("\t")
            rows.append((fingerprint, int(size), name))
    return corpus, rows


def tables():
    """(console, table, settings key) of every table the consoles ship: the first under the console's id (as before
    there could be more), the others under `<console>:<file name>`."""
    for console in consoles.CONSOLES:
        for i, table in enumerate(t for t in (console.sdk_table, *console.sdk_tables) if t):
            yield console, Path(table), console.id if i == 0 else f"{console.id}:{Path(table).stem}"


def _stored(store, key, digest, corpus):
    row = store.one("SELECT value FROM settings WHERE key=?", key)
    stored = json.loads(row["value"]) if row else {}
    current = stored.get("digest") == digest and store.one(
        "SELECT 1 FROM sdk_signatures WHERE corpus=? LIMIT 1", corpus) is not None
    return stored, current


def _load(store, key, digest, console, path, data):
    corpus, rows = _parse(path, data)
    _, current = _stored(store, key, digest, corpus)
    if current:
        return
    bindings = [(corpus, console.arch, int(console.big_endian), name, fingerprint, size)
                for fingerprint, size, name in rows]
    # The app or its worker may have installed this table while we queued. Check again
    # under the shared writer gate, before opening a SQLite write transaction.
    with store.writer_gate.hold():
        stored, current = _stored(store, key, digest, corpus)
        if current:
            return
        with store.tx() as db:
            for old in {stored.get("corpus"), corpus} - {None}:
                db.execute("DELETE FROM sdk_signatures WHERE corpus=?", (old,))
            db.executemany("INSERT OR IGNORE INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) "
                           "VALUES(?,?,?,?,?,?)", bindings)
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                       (key, json.dumps({"digest": digest, "corpus": corpus})))


def ensure(net) -> None:
    """Load every console's table into the shared index once, and again only when the table changes."""
    seen = net.__dict__.setdefault("_sdk_builtin", set())
    for console, path, key in tables():
        try:
            data = path.read_bytes()
        except OSError:
            continue  # (a copy of the app without the table: detection keeps the user's own SDK folders)
        digest = hashlib.sha256(data).hexdigest()
        stamp = key, digest
        if stamp in seen:
            continue
        lock = net.__dict__.get("_sdk_builtin_load_lock")
        if lock is None:
            lock = net.__dict__.setdefault("_sdk_builtin_load_lock", threading.Lock())
        with lock:
            if stamp not in seen:
                _load(net.store, KEY + key, digest, console, path, data)
                net.store.after_commit(lambda stamp=stamp: seen.add(stamp))


def corpora(net) -> set[str]:
    """The corpora loaded from the app's own tables (Settings lists them as built in, not as an indexed folder)."""
    return {json.loads(r["value"]).get("corpus") for r in net.store.all("SELECT value FROM settings WHERE key LIKE ?",
                                                                       KEY + "%")}
