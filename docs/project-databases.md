# Project database isolation

The desktop app and headless `decomp serve` automatically upgrade an existing
home before starting project workers. A successful upgrade uses:

| File | Contents |
| --- | --- |
| `.decomp/decomp.db` | Project catalog, global identities, helper configuration, app settings and compact notification metadata |
| `.decomp/project-databases/<project ID>.sqlite3` | One project's functions, attempts, claims, notes, headers, compiler evidence, session summaries and saved-progress state |
| `<project database>.transcripts.sqlite3` | That project's agent messages, compressed conversation payloads, resumable API conversations and cleanup receipts |
| Existing repair evidence database | Shared immutable repair cases and content |
| Existing match library database | Shared verified reusable functions |

Each project and transcript file has independent SQLite writer admission, WAL,
transactions, read caches and callbacks. Shared repair and reuse databases retain
their existing locations; opening a project never silently creates a replacement
learning archive.

## Automatic upgrade

1. Check the schema ownership inventory and available space. Unknown tables stop
   the upgrade before the original layout is frozen.
2. Back up the consistent original SQLite state to
   `decomp.db.before-project-databases.sqlite3`. Keep this recovery backup.
3. Commit a migration journal and fence operational writes through the old
   catalog. An older process must update/reopen rather than writing stale state.
4. Stream one project's owned rows to staging files. Copy only referenced header
   and text blobs. Validate row content, payload checksums, foreign keys and SQLite
   integrity, then flush and atomically publish the completed files on disk.
5. Preserve the notification cursor and helper identity reservations. Verify all
   completed file checksums before retiring operational rows from the catalog.
6. Commit the new file map and upgrade-complete marker together. Workers start
   only after this publication is complete.

An interruption resumes from the durable journal and immutable original snapshot.
Unpublished staging copies can be rebuilt. The catalog retains its original inode;
its WAL is never replaced underneath other connections. A missing, swapped or
incomplete operational/transcript file fails explicitly. It never opens an empty
replacement or routes writes back into the catalog.

The upgrade needs room for a backup and the new files. Freed catalog pages remain
available for reuse; **Compact database** can return those pages to disk later.
The backup is intentionally retained, so total disk usage does not immediately
fall by the amount moved into project files.

## New projects and removal

New imports reserve a never-reused catalog project ID, then commit their initial
rows independently. A readiness receipt and publication journal recover a process
exit between the project commit and catalog publication. Recovery waits for active
registrations, so another process cannot discard an import still in progress.
Cancelled imports roll back their operational rows and do not publish an empty
project. Reimports keep their existing operational owner.

Removal fences stale operational callbacks and unpublishes the project atomically
in the catalog. An interrupted removal finishes at restart. Removed database files
are retained as fenced recovery tombstones; they are not reused for a later project
or unlinked while another process may still have their WAL open. Shared reusable
matches and learning evidence remain available.

## IDs, ownership and maintenance

Existing public IDs remain unchanged. New operational row IDs use the project's
catalog ID to select a disjoint 32-bit allocation range below JavaScript's exact
integer limit. SQLite AUTOINCREMENT owns allocation without a catalog write and
prevents reuse after deletion or restart. Global activity delivery has its own
monotonic catalog IDs. Global repair review pages use time and ID boundaries so a
newer repair in a lower project range cannot be skipped.

Use `Network.project_store(project)` explicitly. Keep its Store throughout each
action, including locks and after-commit callbacks. Do not parse SQL to choose a
file or use a thread-local current project. Global feeds merge bounded metadata
queries; shared labels are resolved from the catalog before project writes.

Packing, garbage collection, size reporting, retention and compaction discover all
active operational and transcript files. Pruning checks references within each
owner's transaction. Compaction holds one file writer at a time and maintains each
shared learning archive once. Transcript cleanup retains lifetime session/usage
summaries and rejects late callbacks from removed helpers.

Low-level `Network`/`RomNetwork` library construction without an upgrade marker
keeps the legacy layout unless `project_databases=True` is supplied. App startup
always supplies it. Once a home has an upgrade journal, every constructor resumes
or uses that layout automatically, including background worker processes.

To recover manually, stop all processes using the home first and preserve the
failed files. Restore a consistent catalog/project/transcript backup together;
do not mix files from different snapshots. The pre-upgrade SQLite backup contains
the original operational state and allows the upgrade to be retried.
