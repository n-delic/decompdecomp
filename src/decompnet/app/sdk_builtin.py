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
from pathlib import Path

from .. import consoles

KEY = "sdk-builtin:"  # settings key per console: the table's digest and corpus, so a start reloads only a new table


def read(path: Path) -> tuple[str, list[tuple[str, int, str]]]:
    """(corpus, [(fingerprint, size, name)]) of a table: a first comment line naming its source and commit, then
    tab-separated fingerprint, size, name and builds."""
    lines = path.read_text(encoding="utf-8").splitlines()
    source = lines[0].lstrip("# ").split(",")[0].split()
    corpus = f"built in / {source[0]} {source[1][:12]}" if len(source) > 1 else f"built in / {path.stem}"
    rows = []
    for line in lines:
        if line and not line.startswith("#"):
            fingerprint, size, name, *_ = line.split("\t")
            rows.append((fingerprint, int(size), name))
    return corpus, rows


def ensure(net) -> None:
    """Load every console's table into the shared index once, and again only when the table changes."""
    seen = net.__dict__.setdefault("_sdk_builtin", set())
    for console in consoles.CONSOLES:
        if not console.sdk_table:
            continue
        path = Path(console.sdk_table)
        try:
            data = path.read_bytes()
        except OSError:
            continue  # (a copy of the app without the table: detection keeps the user's own SDK folders)
        digest = hashlib.sha256(data).hexdigest()
        if (console.id, digest) in seen:
            continue
        row = net.store.one("SELECT value FROM settings WHERE key=?", KEY + console.id)
        stored = json.loads(row["value"]) if row else {}
        corpus, rows = read(path)
        if stored.get("digest") != digest or not net.store.one("SELECT 1 FROM sdk_signatures WHERE corpus=? LIMIT 1",
                                                               corpus):
            with net.store.tx() as db:
                for old in {stored.get("corpus"), corpus} - {None}:
                    db.execute("DELETE FROM sdk_signatures WHERE corpus=?", (old,))
                db.executemany("INSERT OR IGNORE INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) "
                               "VALUES(?,?,?,?,?,?)",
                               [(corpus, console.arch, int(console.big_endian), name, fingerprint, size)
                                for fingerprint, size, name in rows])
                db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                           (KEY + console.id, json.dumps({"digest": digest, "corpus": corpus})))
        seen.add((console.id, digest))


def corpora(net) -> set[str]:
    """The corpora loaded from the app's own tables (Settings lists them as built in, not as an indexed folder)."""
    return {json.loads(r["value"]).get("corpus") for r in net.store.all("SELECT value FROM settings WHERE key LIKE ?",
                                                                       KEY + "%")}
