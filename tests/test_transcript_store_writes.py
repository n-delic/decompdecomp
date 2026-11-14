"""A transcript database takes writes like any other store: its own setup must give the write path what it reads.

d814b952 taught every store's write path to name the last writer and check the write-ahead log; the transcript
store builds its connection in its own _initialize and had neither, so its first write failed (AttributeError) and a
demo game's import stopped."""
from decompnet.app.transcript_storage import TranscriptStore


def test_a_transcript_store_takes_writes(tmp_path):
    store = TranscriptStore(tmp_path / "transcripts.sqlite3")
    for value in ("first", "second"):
        with store.tx() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('k',?)", (value,))
    assert store.one("SELECT value FROM settings WHERE key='k'")["value"] == "second"
