# Interactive app performance

The desktop app and private web interface use the same local implementation. This change does not add public hosting or external caching.

## Data and invalidation

- `project_totals` stores progress accounting, maintained by SQLite triggers in the same transaction as function changes. Import, reimport, room sync, corrected metadata, moved functions and rollback use the same rules as `PROGRESS_COLUMNS`. Existing databases bootstrap once in an atomic migration.
- Sidebar reads join project rows, totals and indexed last-activity timestamps in one query. Per-project errors remain isolated. Room claims use one batched lookup, including name-based imported projects.
- Constant-time map revisions replace aggregate scans. Relevant source metadata, status, target, names and build membership invalidate the revisions. Target/layout revisions invalidate cross-project byte membership independently of ordinary attempts.
- Per-engine view snapshots retain at most 100,000 row units and 64 entries. Concurrent identical snapshot builds coalesce. Overview diagnostics expire after five seconds; map/cross-project snapshots after thirty seconds. Transactional map reads are not cached. Full map requests also include attempt revisions and current claims in their freshness key.
- Clients opt into changed-row maps. Compatible layouts patch existing objects and draw changed cells; missing old snapshots and structural changes return full maps. Older clients continue receiving full maps.

## Scheduling and compilation

Sidebar/live reads use a separate two-request lane from the four-request heavy UI lane. Both read saved progress while verification runs. They cannot bypass mutation or judge synchronization gates.

Background worker threads acquire a background compiler gate as well as the existing global compiler gate, leaving one global slot available for foreground requests. Cancellation is checked while waiting at either gate. Explicit worker counts still determine search concurrency; they do not remove compiler resource limits.

Only simultaneous identical, deterministic sandboxed ROM compiles share work. Keys include coordinator identity/target snapshot, command/settings, staged header contents, environment, preludes/intrinsics and executable metadata. Results are copied for each follower. There is no persistent compiled-object cache. Cancellation and diagnostic artifact capture bypass sharing; custom commands/mounts/extra include flags and clock-dependent macros are conservative bypasses. Each ordinary request retains its own linking, exact-byte judge, attempt recording and attribution. Imported project builds retain their existing locks and judge.

## Reproduce measurements

```sh
PYTHONPATH=src python tools/benchmark_project_views.py
pytest -q tests/test_project_views.py tests/test_compile_sharing.py tests/test_loading.py
node tests/gui_map_delta.js
```

A scratch synthetic database with 12 projects and 24,000 functions, measured on the development Mac:

| Operation | Median / measured time |
|---|---:|
| Previous per-project progress aggregates | 3.817 ms |
| Stored progress rows, one query | 0.013 ms |
| First cross-project identity scan | 14.726 ms |
| Cached cross-project identity lookup | 0.002 ms |

Read timings use warmed data, with 40 repeats; the cold membership scan is a single measurement.
They measure components, not end-to-end project opening, compiler speed or performance on other hardware.
A separate synthetic run measured 1,000 attempt-counter updates in one transaction at 4.577 ms
with rollups versus 2.274 ms without them (about 2.3 microseconds extra per update). This excludes
per-transaction durability costs and is not a compiler-throughput benchmark. Compilation and
project file analysis can still dominate real workloads.

Regression checks cover totals versus the original SQL after edits/moves/deletion, migration, rollback,
map snapshot/delta/fallback behaviour, fresh attempt counts, shared compile identity and result isolation,
cancellation, and foreground capacity while background/detail requests are blocked.
