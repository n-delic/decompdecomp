# Large database text

Large text (512 bytes and above) in function assembly and C, reuse feedback,
compiler trial evidence, agent messages, per-attempt compiler settings/provenance,
repair frontiers/reports and verified match inputs shares the
checksummed `text_payloads` store with attempt source and repair evidence. Identical
text has one compressed payload.

Progress manifests are the exception. Each publish writes a new revision, so the
manifest (13 MB on a big game) is new text every time. In the shared store every
sync left the previous manifest behind as an unused payload: one big game's
database held 17 GB of them. Now `progress_manifests` keeps one zlib-compressed row
per project (the last published database manifest, the base of a three-way merge).
It is compressed before the writer transaction, replaced in place, and not written
again while unchanged. The folder's own manifest is not stored: it is on disk.
Rows from older versions (`progress_state.disk_manifest`/`projected_manifest`)
are still read. The first write empties those columns and releases their payloads.
Small text, metadata, keys, scores, target bytes and foreign keys remain in place.
No source, evidence, conversations or lifetime metrics are discarded by compression.
The independent match library keeps fingerprints, compiler metadata and project/name/address references.
It does not keep C or headers: reuse loads the current verified inputs from an available donor project.
Indexing and compression maintenance remove legacy source/header copies in cancellable batches;
Compact database file returns the freed pages to disk. Removed donors remain visible as references,
but cannot supply candidates until their project is available again. Structural fingerprint/symbol
metadata can still be deduplicated and compressed.

## Existing databases

New records are compressed automatically. Startup installs only views and triggers;
it does not scan/rewrite existing history. In Settings → Database storage, run
**Compress existing records** to convert older data. It is cancellable and safe to
resume. Batches contain at most 50 records and normally at most 2 MiB of text;
a single larger record is handled alone. Compression/hashing takes place outside
the writer transaction, and conditional updates preserve concurrent edits.

Compression makes old pages reusable. **Compact database files…** returns unused
pages to disk in the main database, the match library, repair evidence, and each
project database (`project-databases/*.sqlite3`) and its transcripts, one file at a time. Run it when workers are idle:
other writers wait, readers remain available, and each SQLite VACUUM is atomic and
interruptible. Stopping between files keeps the first completed compaction. Free
space must accommodate twice the larger file size. There is no automatic VACUUM
or deletion/retention policy change.

## Code and maintenance tools

Text fields physically hold small/legacy TEXT or a 32-byte BLOB containing the
SHA-256 of immutable shared text. `Store` connections route reads through detail
views; SQLite prunes unused expressions, so indexed metadata/count queries do not
decompress content. Lazy table owners install their views/triggers atomically,
and readers opened before those tables detect the installed views. Raw inspection
uses `main.<table>` deliberately. Raw writers must register the payload functions;
application readers must use `Store` so judging, exports and JSON consumers receive
exact text. Update all app/worker processes together before using the new format;
older builds do not understand the new text references.

Do not use physical compressed columns for SQL string/JSON transformations in
UPDATE expressions: load exact text through Store and bind the replacement. Copies
between compressed columns may retain the same reference. Garbage collection
checks every registered compressed column under writer admission.

`payload_links` indexes every reference. The first storage task in Settings
backfills it (`payload_links.collect`). Once every column's backfill is done,
`payload_links.indexed` is true for that file, and two automatic paths start:
- `payload_links.release` deletes a payload that a writer just stopped using,
  inside that transaction, when no link names it. It uses indexed lookups only.
- Every 6 hours (not in the first 30 minutes after start), a background job runs
  the full collection on each indexed file. It runs in the paced maintenance lane,
  in the same 256-row transactions.
Before the index is complete, neither path deletes anything; collection stays a
Settings task. Deleting rows does not shrink a file. There is still no automatic
VACUUM: run Compact database files, or `tools/compact_databases.py` with the app closed. Missing or
corrupt content fails; it is never treated as empty source. A damaged stream fails
zlib's own checksum on every read. Each process also checks a payload's SHA-256 the
first time it reads that payload (known by its hash, compressed length and CRC-32),
so a different payload stored under a hash fails too, without hashing every read.
Decompression caches up to 64 MiB of text, at most 4 MiB per text; large manifests
are not retained in that cache. Text over 1 MiB is compressed at zlib's fastest
level; every level reads back the same way.
