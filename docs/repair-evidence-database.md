# Separate repair evidence database

Full repair records now live in `.decomp/repair-evidence.sqlite3` beside `decomp.db`.
Custom database filenames get their own `<filename>.repair-evidence.sqlite3` sibling.
The operational database retains the small review/search index, annotations, sampling
counters and analysis bookkeeping. Matches and normal attempt history stay operational.

The archive stores immutable, checksum-verified compressed contents and case references.
Frozen snapshots resolve before/after/previous source and headers while historical
inputs are still available, and include the successful attempt score and target bytes
when their hashes agree. Large snapshot strings, including shared headers, are stored
once by hash. Missing historical inputs are recorded in `missing_inputs`; imported
build trees and compiler binaries are not bundled. Archiving cannot reconstruct inputs
already missing before migration.

## Migration and storage

New captures and evidence updates write the archive first, then publish the main index
reference. Both databases use cross-process writer admission. If a main transaction
rolls back or a process crashes between commits, the archive may retain an unreferenced
case; no committed main reference points to an uncommitted archive write. Cases are
immutable and are not automatically garbage-collected across the two databases.

A named **Move repair evidence** startup task migrates legacy inline and compressed
records one at a time. It can be stopped in Background work and resumes next startup.
Opening the database installs only schema and functions, with no bulk evidence scan.
**Compress existing records** also resumes migration, then collects unreferenced shared
payloads from the operational database. **Compact database files…** reclaims reusable
pages in all three databases (operational, repair archive and match library). Migration
alone does not reduce the allocated size of the operational file.

Review, classification, replay and selected-case JSON exports keep their existing APIs.
Metadata-only lists do not open/decompress the archive. Full detail reads use the frozen
snapshot, so deleted or reused attempt IDs cannot substitute unrelated source.
Missing or damaged archive contents fail explicitly, never silently become empty input.

## Export and backup

Settings → Database storage → ⋮ → **Export repair evidence database** makes a consistent
SQLite backup that includes committed WAL contents. The downloaded database also includes
current review annotations from the main index in a `reviews` table. `cases` points to
record and frozen snapshot contents; snapshot objects of the form
`{"$repair_text_sha256":"…"}` reference UTF-8 zlib payloads in `contents`. Hashes are SHA-256.
The export contains all archived cases, potentially including unreferenced cases from
interrupted main transactions; use existing selected-case JSON exports for a smaller set.
Nothing is uploaded automatically. Exports can contain game-derived C, headers, assembly,
target bytes and agent annotations; inspect/select cases before submitting them.

Back up both the operational and repair databases. Do not copy just a live `.sqlite3`
file while its WAL is active; use SQLite backup or this export action. Keep all app and
worker processes on the updated version: older builds do not understand archive refs.
A raw archive backup preserves frozen evidence; the main database remains authoritative
for current review annotations and operational links. Restoring only the main database
cannot recover missing archive contents. The export is portable evidence, not an import
or submission service.
