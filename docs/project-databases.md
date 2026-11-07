# Project database isolation

## Current implementation: storage boundaries

`Network.project_store(project)` selects operational storage using an explicit
catalog project ID or slug. The production implementation currently returns the
existing `Store`; **this refactor does not migrate data or enable separate project
databases yet**. It does not reduce contention by itself.

Function lookup with an explicit project, unit headers and their revision cache,
attempt recording, attempt-turn locks, notes, selection policies, source regression
reads, symbol lookup and project configuration edits now select their project
store. A transaction's callbacks must be enrolled on that same Store. Pass the
scope explicitly into background work; do not change a global or thread-local
"current project" and do not infer the destination by parsing SQL text.

`Network.store` remains the legacy compatibility store for identities, catalog
discovery and unported services. Unqualified function lookup and global feeds
still read that database. This compatibility path must not become a fallback for
a missing or unavailable project database once migration is enabled.

The isolation tests supply an explicit resolver backed by two independent SQLite
files. They exercise concurrent project writes, headers, notes, selection,
attempt commit/rollback and external-match notifications. The test resolver is
not a supported runtime configuration: many services still need conversion.

Project-scoped access also covers the claim transaction, function-specific release
receipts and replay protection, recorded-model pause/retry state, model filters,
candidate history and variant bookkeeping, candidate scope fingerprints and their
revision caches, data-region records, and candidate quality/review settings.
Global quality-report listing discovers catalog slugs and queries one project at
a time, rather than joining all projects' function/settings tables in one file.
Quick-claim request serialization uses its own small in-memory mutex and does not
acquire a database mutex to create a request lock.

Scoped callers now supply `project_id` to stall counts, release guidance, draft
report reads and agent-review settings. Their old ID/key-only forms remain for
unconverted callers and still use the legacy Store. Those compatibility paths,
global claim discovery, and supporting candidate diagnostic services must be
converted before activating project files. No new manifest or runtime switch
enables them early. The shared repair evidence and reuse databases are unchanged.

Saved-progress synchronization, manual backups, manifest rendering, verification
history transfer and automatic Git commit/push bookkeeping also select the owning
project store. Manifest author names are resolved in one bounded query against
the shared agent registry. Autosave discovery reads catalog slugs, then obtains
each project's current save policy and root from its operational store; the
current shared layout retains its cached discovery path without extra project
queries. README function summaries follow the same project ownership.

A thread-local transaction fence prevents long file synchronization while the
calling thread holds a write transaction on any Store. It does not select a
database or block unrelated threads. Tests cover nested stores, rollback,
callbacks, independent save baselines and score-commit acknowledgements.

The room engine now selects project storage for link configuration, mirrored
leases, events, matches, best candidates, notes, headers and layout edits. Link
restoration discovers catalog slugs but reads the current link from each owning
store. Remote author identities remain shared, with transactional insertion that
handles simultaneous room updates for the same author.

Saved-progress restores prepare shared author IDs before taking a separate
project store's write transaction. The legacy single-file path keeps author
creation inside the same transaction as the restore so failed imports still
roll back atomically. The preparation helper refuses cross-store preparation
inside an existing transaction. Tests exercise both layouts and real disk-note
imports, without issuing runner credentials for attribution identities.

## Ownership for the completed split

| Storage | Contents |
| --- | --- |
| Shared catalog | App settings, global agent identities, project IDs/slugs and database locations |
| One operational database per project | Project configuration, functions, claims, attempts, notes, headers, compiler trials, progress, project-specific settings and evidence review/index state |
| Existing repair evidence database | Immutable captured repair cases and content blobs |
| Existing match library database | Verified reusable functions shared between games |

Keep the two existing shared learning databases at their existing locations.
Project database paths must not implicitly relocate or duplicate them. The full
repair corpus is already separate; its operational index and attempt links still
need explicit ownership during the project split.

## Remaining work before enabling automatic migration

1. Convert the remaining engine services, routes, workers and maintenance paths
   to explicit project storage. Separate global settings from the project-owned
   keys that currently share the `settings` table. Audit remaining shared identity
   writes for lock ordering; saved-progress restoration now resolves them before
   separate project transactions. The room transport, snapshots and host/client
   service discovery still need conversion beyond the engine's mirror methods.
2. Replace cross-project operational SQL joins with bounded queries to individual
   project stores. Keep cross-game matching and activity feeds complete without
   loading every project's full history. Preserve global identity and cursor
   semantics, including references from the existing learning databases.
3. Implement automatic migration before workers start. Copy a consistent legacy
   snapshot, preserve IDs and compressed payload references, validate counts,
   foreign keys and payloads, and publish the new catalog locations only after
   successful validation. Retain a recoverable backup and a durable migration
   journal so an interrupted update resumes safely. An error must never open an
   empty replacement database or route writes back into the legacy file.
4. Exercise restart recovery, a migrated room host/client, background workers,
   global feeds, cross-game reuse, cleanup, exports and project deletion against
   migrated databases. Gate activation on those integration tests.

Until those steps are complete, do not add a setting or manifest that activates
partially converted storage, and do not remove project rows from the legacy file.
