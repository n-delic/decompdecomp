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

## Queue and inspector throughput

Deep-pass selection reads at most 128 function input rows at a time, joining pass state and
host claims. It retains only identities and scope hashes while waiting for dispatch. Each
worker reloads the current function and scope before searching; removed or already matched
functions are skipped. Eligibility checks still inspect the actual source/build inputs.

Both ROM and imported-project inspectors return at most 800 chart points. Each of up to
160 ID-range buckets preserves its first/last attempts, best/worst scores and a failed
compile. The chart identifies representative sampling and displays the full attempt count;
all attempts remain stored. Notes load 40 raw records per page, then apply the existing
handoff grouping/filtering. **Load older notes** uses a timestamp/ID cursor and preserves
loading state across polling; changing function or project ignores old responses.

Ordinary deep-search progress snapshots coalesce over 200 ms, including a trailing flush.
New workers, phase changes, improvements, errors and completion publish immediately. Full
technical logs are still recorded. Search progress callbacks use this same coalescing path.

Project GET response encoding and map JSON/gzip encoding run in workers. Full maps reuse
their array representation and encoded bytes while their exact snapshot remains cached.
The wire cache has an 8 MiB encoded-byte budget and 30-second expiry; unique snapshot tokens
prevent ID reuse or cross-project collisions. Delta/unchanged replies are small and encoded
directly. Map arrays count against the existing row budget.

### Optional timing diagnostics

Start a checkout with `DECOMP_PERF=1` to add numeric `Server-Timing` headers and application
log entries for project requests and maps. `queue` measures time before the request worker
starts; `work` includes SQL and other handler work; `sql` is its subset of SQLite read time;
`encode` covers JSON/gzip response construction (including cache hits). Query counts are
logged too. These timings exclude network transport and SQL outside the request thread.
SQL text, bindings, invite URLs and source contents are not included.

For browser diagnostics, run `localStorage.setItem('decomp.perf','1')` in its developer console.
Subsequent API calls log total fetch/JSON time and server timing headers. Function rendering,
charts and map drawing log local durations (map animation samples at most once a second).
Disable with `localStorage.removeItem('decomp.perf')` and by removing `DECOMP_PERF` on the server.
The browser diagnostics remain in its console.

Run `PYTHONPATH=src python tools/benchmark_function_history.py` for a scratch-only synthetic
comparison. On the development Mac, 20,000 attempts produced 20,000 points / 786,721 JSON bytes
with the full-history path versus 485 points / 24,248 bytes with sampling. Median query,
Python conversion and JSON encoding time over 12 warm runs was 12.03 ms versus 10.44 ms.
This is a component benchmark: it does not measure browser rendering or claim faster judging.


### SQL phases in agent tool transcripts

MCP tool response timings include `sql_connection_ms`, `sql_lock_wait_ms`,
`sql_execute_ms` and `sql_fetch_ms`, summed across the call's database reads.
Connection and execute durations are retained when a read raises an error,
including a SQLite busy timeout. `sql_lock_wait_ms` measures the fallback read
connection's Python lock; SQLite's own busy wait is included in execute/fetch,
not that lock field. These numeric fields contain no SQL text or bindings.
They supplement `sql_read_ms`, `sql_reads`, writer-lock and candidate-lock timings;
they do not account for every part of dispatch or identify a blocking writer.


### Saved-progress synchronization waits

Agent tool transcripts also record `progress_project_lock_wait_ms` and
`progress_verification_wait_ms`: time waiting to enter the project's progress
synchronization lock and the shared verification slot. `progress_sync_ms` is the
inclusive synchronization duration (including both waits, work and cleanup),
retained on early return and failure. These durations accumulate per request;
they are not CPU percentages. Do not add the inclusive total to its wait fields.
The lock ordering and verification requirements are unchanged. These measurements
help distinguish waiting behind another saved-progress check from work in the
current check; they do not establish the owner or cause of every dispatch delay.
