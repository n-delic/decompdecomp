# Large database text

Large text (512 bytes and above) in function assembly and C, reuse feedback,
compiler trial evidence, agent messages, per-attempt compiler settings/provenance,
repair frontiers/reports, verified match inputs and progress manifests shares the
checksummed `text_payloads` store with attempt source and repair evidence. Identical
text, including the disk/projected copies of a manifest, has one compressed payload.
Small text, metadata, keys, scores, target bytes and foreign keys remain in place.
No source, evidence, conversations or lifetime metrics are discarded by compression.
The independent match library also compresses its already deduplicated text.

## Existing databases

New records are compressed automatically. Startup installs only views and triggers;
it does not scan/rewrite existing history. In Settings → Database storage, run
**Compress existing records** to convert older data. It is cancellable and safe to
resume. Batches contain at most 50 records and normally at most 2 MiB of text;
a single larger record is handled alone. Compression/hashing takes place outside
the writer transaction, and conditional updates preserve concurrent edits.

Compression makes old pages reusable. **Compact database files…** returns unused
pages to disk in the main database and match library. Run it when workers are idle:
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
checks every registered compressed column under writer admission. Missing or
corrupt content fails checksum verification; it is never treated as empty source.
Decompression caches at most 32 texts of at most 256 KiB each; large manifests are
not retained in that cache.
