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

Host ROM and source-project snapshots, event pagination and contributor rollups
now read the owning operational store. Snapshot author labels are resolved with
bounded shared-registry queries rather than joins to project-local identity
copies. Guest cursor/cache markers, queued saved-progress transfers, host compiler
inputs and source-tree heads also use project storage. Cursor/queue/head writes
read current configuration within their write transaction, preserving concurrent
configuration edits. The existing constant-time attempt high-water query runs on
the selected store; cursor bounds are captured before reading incremental work.

Guest batch imports prepare shared remote identities before opening a separate
project transaction and roll project changes back as a batch on failure. The
legacy layout retains its transactional identity rollback. Room discovery reads
operational link configuration after catalog slug discovery, without adding
per-project configuration queries to the legacy single-store path.

Host membership changes now keep identity mutations in the catalog. When project
storage is separate, the same catalog transaction queues project claim cleanup
and its membership event in `room_membership_effects`. Delivery commits the
project changes with a small receipt before acknowledging the catalog queue.
Receipts prevent duplicated events if delivery is interrupted or two readers
replay the same item. Missing project state leaves the handoff queued. Delivery
defers while the caller holds any Store transaction and resumes on subsequent
room reads or restart. The legacy file still applies membership changes and
effects in one transaction without creating an outbox or receipts.

Pending room submissions, packet/baseline reads, retry claims, queue status and
legacy recovery now use their project store. Queue updates and resends validate
the explicit project scope. Prepared-bundle verification and its build-input
snapshot use the same project storage; the linked-byte verdict and atomic
profile/header/source publication are unchanged. Room claim expiry resolves
machine identity IDs from the catalog before a separate project transaction.

Local worker holds and releases, speculative permutation turn accounting and
permutation-input renewal now select the owning project. Separate-store expiry
resolves machine and legacy speculative identities before entering the project
writer transaction, without depending on stale project copies of agent names.
Input renewal rejects mixed-project batches before writing fingerprints.

Compiler map inputs, comparison eligibility/history, profile usage, suggestion
reads and compiler configuration publication now select project storage.
Configuration changes still verify affected matches and reject concurrent input
changes before publishing settings and rescored attempts together. Claim checks
resolve shared identities before acquiring a separate project writer.

Judge bookkeeping, local draft/source preparation, polishing, function editing,
briefs, boundaries and coverage audits now select project storage. Project GUI
maps, summaries, history and live activity read that same storage; public labels
come from the shared agent registry. Separate-store peer indicators compare
cached target fingerprints one project at a time. Global notifications, imports and remaining cross-game services still need their audit.

Imported-project runtime reads/writes now select project storage for file edits,
score refreshes, local drafts, claims and inspector history. Compiler application
batches and matching preflight diagnostics keep their transactions, receipts and
after-commit callbacks on that same store. Global function-name discovery and
stopped-worker claim recovery visit project stores explicitly. Project creation,
removal and the remaining cross-game services still require conversion.

Twin indexes, donor shortlists, cross-game transfers, local misses and repair
mapping evidence now distinguish source and destination stores. Result labels
read source metadata from its owner; archived transfer labels survive removal.
The shared match library retains its catalog-relative path and identity, while
snapshots and publication callbacks follow the owning project transaction.
Older ID-only provenance uses indexed project-owner discovery. Global ID
allocation and migrated-schema validation remain prerequisites for activation.

Helper session summaries, conversation resume state and efficiency snapshots
use the owning project file. Large transcript messages, score-event receipts and
transcript cleanup markers use a dedicated transcript database alongside each
project file (`<project database>.transcripts.sqlite3`), with their own writer
gate and compressed payloads. Transcript storage has no function/attempt tables.
The production legacy layout is retained until automatic migration is ready. Helper identities/configuration and
provider account cooldowns remain shared. Cleanup resolves shared identities
before each project's transaction, retains lifetime session summaries and rejects
late transcript callbacks after removal. Restart recovery closes interrupted
sessions in each operational file. Model filters resolve shared agent IDs before
querying project attempts; no copied identity labels are needed.

Deep and background permutation searches now select their project for trial
caches, buffered bookkeeping, pass signatures, metrics, input-effect costs and
paused-run controls. Offline deep-room delivery queues follow the room project.
Shared SDK corpora and toolchain capability records remain global. Game knowledge,
its model support, evidence and review queues are project-owned; display names
still resolve from the shared registry. Source recovery, candidate-origin reports,
random-search checkpoints and bounded proposal cursors use the same explicit scope.

## Ownership for the completed split

| Storage | Contents |
| --- | --- |
| Shared catalog | App settings, global agent identities, project IDs/slugs, database locations and durable membership handoffs |
| One operational database per project | Project configuration, functions, claims, attempts, notes, headers, compiler trials, progress, project-specific settings, membership delivery receipts and evidence review/index state |
| One transcript database per project | Recorded agent messages, compressed conversation payloads and cleanup receipts |
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
   separate project transactions. Host membership and room verification now have
   explicit boundaries; other claim-expiry callers, provider services, source
   preparation workers and repair indexes still need conversion. Remaining compiler
   consumers outside the compiler service also need auditing.
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

Repair review records, attempt settings/provenance, profile indexes and outcome
queues now select the project store. Global review pages merge bounded project
pages and exports stream stable IDs from their owners. Repair analysis starts
only after the capture transaction commits and drains queues through one worker.
Sampling and analysis preferences remain global. Store readers explicitly reuse
the configured shared repair archive, so opening a project file cannot silently
choose a new archive location. New project stores must receive the catalog's
archive instance before their schema or captures are prepared.

Durable source-recovery queues now restore per-project state and resolve their
shared worker identity before cleaning that project's claims. Reuse-cascade
notifications read the donor and each destination independently. Cross-project
function peers shortlist metadata per store, select at most 400 candidates overall,
then load target bytes only for that shortlist. Compiler range checks, profile
cleanup, SDK naming revisions, address lookup and match reconciliation use explicit
project storage. Repair schema initialization happens within an already admitted
record transaction, preserving cancellable writer waits without recompiling.
