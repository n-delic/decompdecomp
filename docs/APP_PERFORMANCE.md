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


Synchronization transcripts also expose `progress_stat_ms` (file stat checks),
`progress_files_ms` (saved-file capture),
`progress_manifest_ms` (manifest validation), `progress_render_ms` (database render),
`progress_import_ms` (context/layout application), `progress_publish_ms` (file publication),
and `progress_saved_judge_ms`
(saved verification, including waiting for its worker). These phases are inclusive
and can overlap nested operations; do not add them to `progress_sync_ms` or interpret
them as CPU time. The saved-judge worker has its own execution context, so its SQL
reads are not included in the request's coordinator SQL counters. This separates a
long recheck from slow file capture/rendering without suppressing either operation.
No source, filenames or manifest contents are added to the timing fields.


### Unchanged progress checks

Saved-progress synchronization keeps its per-project lock while checking dirty
state, configuration and existing file signatures. If those checks establish an
unchanged project, it returns without taking the shared verification slot. This
also applies to forced checks that verify an unchanged full file signature.
After a restart, there is no in-memory signature: the check instead reads and
hashes all saved files against the durable baseline. An identical snapshot can
return or publish local database changes through the existing guarded publisher
without waiting for another project's bulk recheck. Changed snapshots still enter
the shared import/verification lane; project metadata, pause and cancellation state
are rechecked after waiting.
This removes an unrelated-project queue from no-op checks. It does not bypass
same-project synchronization or guarantee that a real import/full recheck is fast.

### Progress filesystem failures

Rejected links and non-regular progress paths identify their relative filename.
Failed synchronization also reports a bounded, escaped OS filename and error code
when available, without printing arbitrary exception arguments or source contents.
These diagnostics preserve the original failure and all synchronization guards;
they do not establish the cause of long per-project lock waits or retry failed work automatically.

### Files disappearing during progress capture

A listed file can disappear while git or an editor updates the progress folder.
A missing-file error during capture now restarts the read from the manifest, up to
three attempts, discarding the partial dictionary each time. This retries only file
reads: imports, writes and transaction bodies are not replayed. Successful reads
still pass the normal manifest, snapshot and exact-judging guards.

Persistent disappearance propagates its last error. Permission failures, symbolic
links, non-regular files and conflict markers still fail. This covers a reproducible
read race; it does not prove the cause of every historical missing-file failure or
resolve the separately observed database/project-lock contention.

### SQLite writer acquisition

The app and its intended permuter child can contend for the same WAL database.
Ordinary `Store.tx` acquisition now waits for SQLite in at most 100 ms slices and
releases its local writer mutex between attempts. The configured busy timeout is
one deadline from the first acquisition attempt; zero remains immediate. The
original timeout is restored before running the body and after failed acquisition.

Only `BEGIN IMMEDIATE` acquisition is retried. Transaction bodies, commits and
callbacks are never replayed. Nested transactions and explicit cancellable
maintenance waits retain their existing behavior. This removes one local queue
amplification; it does not eliminate external contention, long transactions or
project synchronization waits. Controlled contention tests are not a live-app benchmark.

## Reject introduced pointer captures with a possible uninitialized read

The shared native generator checks simple introduced integer and pointer locals
before candidate compilation. Pointer captures need the same must-initialization
analysis: assigning a pointer inside `if (count > 0)` does not initialize it on
the other branch. Writing through a pointer reads that pointer, rather than
initializing it. Fully initialized captures remain available to the search.

This applies to users of the shared generator in ROM and PROJECT mode, without
depending on an architecture or compiler. It is a bounded syntactic guard, not
a C equivalence or undefined-behavior checker. Qualified, escaped, shadowed,
unknown pointee aliases and unsupported expression shapes retain no verdict.
An accepted candidate still needs the ordinary compiler and exact judge.

Replaying a private copied 1,000-emission run rejects ten additional possible
uninitialized pointer captures that had reached compilation. Two manually
confirmed bad captures are rejected; the reviewed valid field-address capture
remains allowed. This is evidence for that run, not a general match-rate or
performance estimate. Generated game sources and replay artifacts stay private.


## Keep native initialization verdicts independent

An unsupported embedded assignment to one introduced temporary no longer cancels
analysis of another binding. The guard shares one bounded control-flow graph and
computes a separate must-initialization verdict for each eligible integer/pointer.
A local record typedef containing only scalar fields and literal array bounds is
ignored as a type declaration, with offsets preserved. It does not add executable
reads. Complex fields, macro-expanded declarations, variable bounds and aggregate
initializers remain opaque; the shared lifetime-repair parser is unchanged.

A private replay of 640 retained candidate sources previously gave no verdict for
all of them because of a local matrix typedef. The revised guard rejects 18 possible
uninitialized captures, including the reviewed pointer assigned only in an earlier
possibly empty loop. Original and layout-restored baseline sources remain allowed.
These are skipped unsafe compilations, not new exact matches or a measured runtime
speedup. Floating temporaries and semantic equivalence remain outside this guard.


## Expose simple byte-field macros before native mutation

The shared ROM/PROJECT native input preparation recognizes unique, unconditional
accessors shaped like `FIELD(base, offset)` whose entire body dereferences a typed
pointer at a byte address. Nested loads and assignment destinations become real
memory expressions in the parser instead of opaque function calls. Parameter
evaluation counts, types, byte scaling, definitions and physical lines are retained.
This changes search visibility, not the exact judge or compiler profile.

This is deliberately not a C preprocessor. Conditional/include/undef directives,
redefinitions, stringification/pasting, line observers, splices, unsupported bodies
and multiline invocations retain their original form. Unknown macros and ordinary
functions are not guessed. The existing special m2c expansions remain separate.

A private copied function exposed44 accessor calls, including7 assignment lvalues;
only11 real helper calls remain. Full translation-unit AST/declaration equivalence
and ordinary/diagnostic period-compiler checks preserve all1560 linked baseline
bytes. The separately bounded search found no new exact match. No general speedup
or match-rate improvement is inferred; the verified gain is access to previously
hidden memory/address expressions.

### Guarded values before spending agent turns

Deterministic search now offers `guarded_call_value` in both ROM and imported
project modes on supported 32-bit C targets. It keeps captures and conditions,
then tries a value already established by a dominating equality in direct call
arguments. It also follows a leading same-width local copy and can reuse an
unchanged comparison operand. Unknown writes, escapes, shadows, macros, labels
inside the arm, reflected types and unsupported ABIs stop the proposal.

A copied caller with corrected byte parameters went from 825 to exact without
an agent edit using this family. This is a controlled replay of an existing
match, not a new game match or a claim about agent token savings. Ordinary and
diagnostic outputs agreed. Search budgets and exact acceptance remain unchanged.

### Routine maintenance after project database migration

- Saved-file reconciliation caches at most 8 MiB of validated UTF-8 contents. It still
  checks filesystem identity, size, modification/change times and file type before
  reuse. Changed files are read and validated; deletions and symbolic links retain
  their existing handling. Publication acknowledgements still reread disk contents.
- Automatic match-library indexing keeps per-function receipts. Match-only changes
  skip normalization, tokenization and archive writes for unchanged functions.
  Compiler configuration, context, symbol and fingerprint changes invalidate receipts;
  explicit indexing still rebuilds every eligible function. Imported projects retain
  their file-based checks. Receipts are published after successful archive writes.
- **Header maintenance → Reclaim catalog space after migration** compacts only the
  catalog. It does not open project databases or compact the shared archives. The
  original pre-migration recovery backup is retained. Compaction remains an explicit,
  cancellable job: catalog writers wait during VACUUM; ordinary startup does not.

### Always-on UI delay history

**Background work → Recent UI delays** shows the latest twenty recorded slow operations.
The server retains at most 100 request/stall samples in memory, discarded on restart.
Requests and event-loop delays of at least 500 ms are recorded. Request durations include
worker waits and response delivery; they do not identify CPU cost. Event-loop sampling
runs once per second and stops with the server. Log messages are limited to one per
route/type per minute. Only registered route templates, durations and timestamps are
kept, never URLs with query values, room invites, SQL or source text. The authenticated
`/api/performance` endpoint exposes the same bounded history for local diagnostics.
Use the opt-in timing diagnostics above to break down queue, SQL and encoding costs.

### Foreground tools during a saved recheck

Direct API and MCP tool entry both enter the existing foreground scope before
synchronization or named-input resolution for ordinary submitted compiles, patches,
variants, notes and plain lease releases. They can proceed only after the recheck
has installed its context and verification evidence, and only while the captured
saved-file signature is unchanged. The normal engine judge still records every
candidate result. Notes and their publication marker share the project lock so a
finishing recheck cannot acknowledge a note without saving it.

Context/layout edits, prerequisite pivots and no-C verdict releases still wait for
the full import. Outside an active ready recheck, the usual synchronization runs.
This removes a wrapper-level queue; it does not skip imports or speed up the bulk
verification itself.

Current `get_candidate(base="current")` and `get_assembly` reads use the same
ready-recheck lane. They read the installed database inputs and retain normal
baseline verification or explicit historical/unverified labels. A read does not
promote saved C merely because a saved recheck is pending. Recovery selections
(`saved_best`, `last_compiling`, `reported_corrected`) remain serialized, as do
ordinary imports when no ready recheck is active.

### Scheduling owners and cleared transcript history

Candidate-scope construction resolves the operational owner once per function and passes that
store to its nested header/version reads. Each function still checks the catalog; a scheduling
batch cannot retain access after project removal or reuse another store's cached inputs.

In separate transcript databases, retention seeks distinct session IDs through the existing
`(session_id,id)` index before fetching session summaries by primary key. It skips already-cleared
history without scanning each message of long transcripts. Removed-helper identity and completed
session cutoff rules remain unchanged; session summaries and lifetime costs are retained.

### Shared lease identities and project removal

Speculative lease cleanup caches derived agent IDs against a durable catalog identity revision.
Insertions, deletions and name/owner/ID changes invalidate it, including external commits.
Transaction-local reads bypass publication to the shared cache; callers receive their own set.
Token validation and project ownership checks are separate and remain uncached here.

Autosave discovery can race a project removal after its catalog snapshot. It discards that row
only when a fresh catalog lookup confirms removal, allowing worker reconciliation to cancel
its old worker and continue other projects. Owner errors for catalogued projects still surface.

### Maximum-throughput deep search

On a dedicated machine, enable **Maximum throughput** in Deep permuter's advanced
settings (or the individual function's deep-search form). Leave **Functions at a time**
and **Concurrent compiles** blank to use the detected CPU core count. A 64 GB M4
needs no machine-specific hard-coded core count: M4 variants have different counts.
The individual-function default of one path expands to the detected core count in
maximum mode; an explicitly higher path count is retained. Time and candidate budgets
still cover the entire function, including parallel paths and speculative compiles.

Each path uses the existing dedicated, persistent AST generator process. A project
pass shares one bounded compiler pool across functions, retaining private prepared
headers and object caches per compiler worker; target caches remain private to each
search lane. This removes the four-worker per-search
compile-ahead ceiling and avoids repeatedly destroying preparation between functions.
Balanced project passes retain the normal global compiler allowance. Maximum mode
raises global admission for its lifetime, bypasses the background-only compiler slot
reserve, and avoids lowering its workers' priority. Multiple searches still share one
global admission counter; ending one cannot replace a semaphore or forget running
compiles. A smaller explicit compile limit bounds that search's synchronous and
speculative compiles. Per-compile memory, runtime and output caps remain in force.

Advisory miss statistics batch up to 256 statements / 4 MiB / one second in maximum
mode, then flush on exit or cancellation. Verified improvements retain immediate
publication through the ordinary current-input and byte-verification guards.

The live strip shows active compiler slots, considered candidates/sec (including
cache hits and rejected candidates) and actual compiler invocations/sec over a rolling
five-second window, cumulative
compiler queue/setup and advisory-history write time, and app-process CPU averaged over the run. CPU excludes
compiler/generator child processes; use Activity Monitor for the machine's total CPU.
Counters contain numbers only and are scoped to the shared search pool. Imported
builds still own their build system and may serialize internally; maximum mode cannot
guarantee full CPU utilization for those projects or I/O-bound workloads.


#### Local-repair candidate supply

Maximum mode also runs the pure targeted-repair generator in persistent private Python
processes. These workers receive captured source, header, assembly, flags, table,
callee and profile evidence over pipes; they never open the project database, invoke
compilers or publish a result. Identical requests across parallel paths share one
future and bounded raw-proposal cache (16 completed batches / 32 MiB). All candidates
still pass the parent's ordinary current-input checks, compiler and exact judge.
Active proposal workers are shown separately from active compiles. App CPU excludes
both kinds of child process.

Partition initial local proposal queues by search path before expensive feedback
ranking, rather than making each path sort and discard every other path's work.
New feedback-generated children retain the existing ownership guard. Worker waiting
checks cancellation and the original search deadline; shutdown terminates proposal
processes and releases their pipes. Specialized cooperative generation sessions retain
their in-process path so family/budget filters are unchanged.

### Autopilot draft pipeline

Explicit autopilot lanes keep a private pinned m2c interpreter warm across functions,
recycling it after 64 requests. Automatic runs keep the ordinary CLI path until
promoted by the user. Stop and timeout terminate the generator process; an unavailable
worker falls back to the pinned CLI. A local 12-request small MIPS draft check took
1.35 seconds through separate CLI launches and 0.17 seconds through the warmed
interpreter, including its first startup, with identical output. This measures draft
generation, not total autopilot throughput.

Each trial retains the saved-file revision guard but leaves project-wide file
synchronization to the watcher. Verified results are saved immediately. Optional
reuse/archive follow-ups run separately in bounded batches and index the changed
functions rather than rescanning the project. Advisory draft reports share bounded
trial bookkeeping; completed resume receipts flush immediately. Stopping a lane
flushes advisory reports and queues follow-ups for its already committed results.

Autopilot gives every machine draft its turn before running the saved-source return
repair lane. The second phase re-reads eligibility and acquires fresh claims, retains
its existing per-function search bounds, and reports separate repair progress with
the same Stop control. Direct single-function calls retain their saved-source repair
behavior. Refresh functions by ID inside the pass rather than resolving them through
project routes, which can trigger saved-file synchronization.

Try raw m2c output before generating optional variants or collecting callee evidence.
Autopilot trials use the existing lean search evaluation: full linking and exact-byte
verdicts remain mandatory; export/address/allocation diagnostics are generated by
interactive evaluation when requested. Pass workers share half-second claim cleanup
and identity reads. Selected expired or legacy speculative claims force immediate
cleanup, and the selected claim is still checked in the acquisition transaction.

### Releases queued behind a compile

A plain agent release takes the per-agent/function candidate lock before retaining the project
publication lock. Compiles use that same order. This lets an in-flight compile publish while its
release waits, preventing a cycle where the release owns the project lock and waits for the
compile's candidate lock. Release receipt checks and note-publication markers remain inside their
existing guards. Candidate-lock waits at tool entry are included in `candidate_lock_wait_ms`.

### Autopilot setup and timing totals

After the full linker confirms an exact result, the machine-draft lane saves it
without constructing unused instruction-diff rows. Uncertain and failed verdicts
still use ordinary scoring; interactive views still obtain detailed diffs.

Worker sessions cache up to 16 resolved native pinned compiler configurations.
Profiles, environment, exact install-manifest bytes (including dependencies), and
runtime/executable stamps invalidate this cache. Custom commands, user kits, Wine,
WSL and VM configurations retain ordinary resolution. This cache contains command
preparation only, never a verdict. The existing compiled-object cache still checks
its complete inputs separately.

Header preparation reads exact source and staged bytes on each use, including edits
with unchanged size and restored timestamps. It updates changed files individually,
restores compiler-written headers and removes unexpected outputs and symlinks. A
context.h edit no longer rewrites every other header. A local synthetic check with
200 small headers and 30 context changes took 1.201s before and 0.505s after. Resolving
a native pinned compiler configuration 100 times took 0.603s before and 0.171s with
worker-local reuse. These measurements cover setup only, not end-to-end throughput.

The autopilot panel's collapsed Timing totals disclosure reports generation,
compiler preparation/execution/wait, linking, scoring, database reads and database
writes for the current machine-draft and saved-source repair phases. Values are
cumulative worker seconds, may overlap, and may exceed wall time with parallel work.
Only numeric totals are retained; a new run resets them, Stop retains them, and
refresh/reopening reads them from server state or the saved last-run summary.

### Hourly follow-up: full-brief and helper startup regressions

A full function brief also serves base `Network` callers (for example room/library
clients), which do not run the GUI engine constructor. Function-specific knowledge
now obtains the same lazy, project-scoped service used by progress saves; concurrent
first use creates one service. It preserves historical attribution and leaves source,
claims and match status unchanged. Synthetic tests cover both legacy and migrated
project databases; this corrects a reproduced crash, not a measured latency gain.

Saved Cursor helpers with the earlier `cursor` ID resolve to `cursor-cli`. Family
selection separates the longest registered ID or alias from a helper name, so
`cursor-cli-claude-…` is attributed to the model family rather than `cli`. CLI messages
that explicitly require authentication or say `use /login` enter the existing
signed-out handling before the fatal-error/reconnection fallback.

### Deep-permuter local pipeline

Maximum-mode proposal generation splits independent pure generator stages across
up to four persistent processes. Identical search lanes share each shard's bounded
future/cache. Completed shards arrive independently, so local candidates can be
compiled and judged while slower families are still being generated. Nested stages
stay with their parent generator, preserving macro and combined-repair coverage.
Arrival order can change which hypotheses fit inside a finite search budget.

The adaptive local loop uses lookahead windows of at most four independent ROM
compiles and primes their history lookups in one bounded read. Judging, feedback and
publication stay on the search lane. Reordering or a new parent closes the old window;
queued work is cancelled and discarded running work is charged to the original
candidate allowance. Imported builds, multiple-profile trials, external inputs and
unsupported compiler-ahead routes retain their ordinary synchronous behavior.

Pending feedback nodes retain their parent/family hashes until the proof changes,
rather than reconstructing and hashing full parent C for every queued candidate on
every trial. A synthetic 500-candidate, 30-reordering check preserved ordering and
took 1.420s before versus 0.069s with retained metadata (20.5x for ordering alone).
Repeated candidate identity requests share a 64-entry / 8 MiB worker-local cache;
complete captured inputs, revision, header, environment and repair rules still
invalidate it, and persisted history signatures remain unchanged.

After a lean link, winner/baseline diagnostics may reuse that thread's link result
only for the identical object, function snapshot, project config, input revision
and symbol/layout generation. A changed or unavailable guard runs the full linker
again. This avoids relinking solely to build advisory diagnostics; it never reuses
a historical match verdict for a new candidate or authorizes publication.

### Verification admission races and regression repair

A project with no saved-ROM verification work (PROJECT mode, no ROM fingerprint,
or no imported context) returns before the shared verification lane. Previously it
could wait behind a large unrelated ROM recheck before `_sync` returned immediately.

A foreground request already waiting for admission now rechecks whether its own
project's importer has installed the new inputs and restored saved evidence. Once
that recheck is ready, the request uses the existing foreground lane, retains the
full saved-file signature guard and leaves the active verifier's semaphore owned.
Not-ready imports still block; a racing file edit is rejected. A queued foreground
request also preserves the active import's progress indicator when it leaves.
Seven bounded regressions fail the old policy and pass the corrected policy while
the unrelated verification slot remains held. This demonstrates removal of these
queue blocks; it does not measure the running app's end-to-end latency.

Admission waits longer than five seconds report the actual lane holder, elapsed
time and current phase in the private app log, at most once per waiting call every
30 seconds. No source text or SQL payload is logged. Cancellation, pause and exact
verification rules remain in force.

Contributor summaries and map recency read numeric history directly from the
selected operational database's physical tables. They avoid expanding source-bearing
read views; existing regressions prohibit source reads and verify covering indexes.
The overview also reuses a mode-specific library report, including a reported absence,
instead of computing a duplicate report before discarding it.

### Deep-search scoring overhead

Candidate disassembly uses per-thread decoders and an eight-function instruction
cache (32 KiB machine-code limit per entry), keyed by bytes, ordered relocations,
architecture and CPU. Diagnostic source mapping reuses these candidate offsets.
Relocation lookup indexes offsets while preserving original-list precedence.
Ordinary losing random rewrites calculate the same score without constructing
diagnostic rows; winners, baselines and local adaptive feedback retain full rows.

Proposal pipes send each source once with its family label. Parent-source blanking
and shared integer-repair views reuse only the latest input; callers receive their
own mutable view. Local speculative depth starts at two, grows to four when all
tickets were consumed, and shrinks toward one when feedback invalidates pending
work. Cancelled/running work retains the original budget accounting.

### Incremental notification source cursors

Each project's notification cursor now uses a covering `(project_id,id)` event
index, including existing databases when reopened. The project/time index remains
for recency queries. Without the ID index, every idle `MAX(id)` poll scanned that
project's historical events; pagination could revisit that history too.

A bounded scratch fixture with 60,000 routine events reduced a warmed maximum-ID
lookup from about 300,000 SQLite VM operations to fewer than 100 (approximately
2.8 ms to 0.02 ms on the development Mac). This measures one query, not total feed
latency. New-event ordering, delivery cursors and transaction boundaries are unchanged.
It does not establish that the separately observed multi-minute saved-source reads
or project-lock waits are resolved.

### Saved-manifest metadata under Python CPU contention

Partial progress saves still read one SQLite snapshot, but return metadata in JSON
pages of at most 128 functions before expanding them in Python. This reduces
native SQLite/Python row handoffs: 1,500 function records now cross that boundary
as 12 rows. Names, numeric addresses, nullable fields, source-presence flags and
function ordering are preserved. Metadata reads never decode saved C; full-source
exports retain their existing checksum-validated detail reads.

A bounded synthetic fixture with 10,000 functions and a competing Python CPU thread
measured median metadata fetch/conversion time of **1.81 s before, 0.150 s after**
over three paired trials. Idle medians were 25.7 ms and 29.4 ms. Scheduling varies:
old contended times ranged from 1.55 to 2.22 s, new times from 0.085 to 0.275 s.
Smaller 1,000-function repeats did not show a consistent median win. JSON transport
adds work when there is little contention; this targets large contended reads.
These are component measurements, not a prediction of live-app speed. The fixture
reproduces GIL handoff contention, but does not establish the cause of every observed
multi-minute source read or project-lock wait. Those remain separate investigations.

Thread-local query readers also avoid acquiring their connection lock during
interpreter finalization. CPython may already have stopped the daemon that owned
it, so a destructor cannot wait for that daemon to release it. Normal worker
teardown still closes the reader. A synthetic child process previously hung at
exit with a daemon holding that lock; it now exits normally. This does not bypass
writer commits or the application's graceful worker shutdown.

### Room reconciliation uploads and host verification

Updated room clients upload up to four fresh bundles concurrently and do not wait
for compilation before sending the remaining inventory. The host acknowledges only
after source, header/compiler evidence and queue references are saved. Uploaded
submissions verify independently of client connections, with four host checks
shared across all rooms/clients. Reopening an active room resumes its persisted
queued or interrupted in-flight submissions. A host Stop stops queue dispatch;
already-running checks can finish, and uploaded evidence remains available.

The host still validates requested ownership/target identity, registered compiler
settings and fresh linked bytes before publishing any match. The older synchronous
apply route remains compatible. Inventory pages use one bounded target query;
progress polling does not rewrite unchanged queue state. Clients and host both
need this version for queued uploads.

### Repeated saved-file scans larger than the cache

A saved-file read now protects its cache hits and newly admitted files for that
scan. Once the content budget is full, uncached tail files are read normally
without evicting that scan's entire warm prefix. The next scan may replace entries
it has not used, so opening another project can reuse the same bounded cache.
The existing device/inode/size/time/mode keys, conflict checks and symlink refusal
still apply to every snapshot. The shared content budget remains 8 MiB.

A bounded synthetic 9 MiB folder (36 files of 256 KiB) was read three times after
warming an 8 MiB cache. The former sequential LRU policy reopened all 36 files
and read 9 MiB each time; scan admission reopened four and read 1 MiB each time.
This measures file operations and bytes, not live synchronization latency.
Full-source database fetches and verification-lane occupancy remain separate
investigations. A proposed JSON-page extension to full-source database reads was
rejected after its synthetic contended median regressed from 0.145 to 0.657 s;
no such change shipped.

### Bulk saved-header snapshots

Full progress exports and partial exports that include headers now resolve their
requested unit headers in one SQLite statement. Latest-version metadata is selected
before decoding content; missing units share the ordinary project-header fallback.
Only effective versions pass through the existing checksum-validating, version-pinning
header view. A concurrent header edit cannot mix versions within this snapshot.
Metadata-only partial saves do not load headers.

A synthetic project with 601 current headers exceeded the 128-entry per-unit read
cache: each repeated export previously issued 1,202 reads. The bulk path issues one.
Three paired warm trials measured medians of 15.1 ms before and 5.1 ms after for
header resolution alone. This does not measure full save latency or establish that
multi-minute project-lock and verification-admission waits are resolved. No cache
capacity, timeout, judge or validation rule was relaxed.

Wrapper-family discovery now projects only donor identity, target bytes, relocations
and matched C. Eligibility uses physical function rows; matched C still passes
through its checksum-validating view. An unrelated packed assembly, seed or best
attempt is never decoded for this shortlist. A 200-wrapper synthetic fixture
reduced payload-read callbacks from 1,000 to 200 with identical retained donor
identities, targets, relocations and C. This measures callback counts, not a claim
that every observed slow wrapper query is resolved; candidates retain the normal
destination judge.

### Independent catalog and project history batches

Advisory search writes retain separate per-store buffers under one aggregate byte,
statement-count and time limit. Switching between catalog policy statistics and
project-local trial history no longer flushes either database. Explicit reads flush
only their own store; winners remain immediate, and search exit/cancellation flushes
pending misses. Successful store commits are removed before another store is tried,
so a later failure cannot replay an already committed batch. Consecutive identical
statements use executemany without reordering different SQL statements.

A synthetic on-disk WAL/FULL-synchronous two-store workload (100 alternating
candidates, 200 retained rows) took 0.2742s / 200 commits before and 0.0291s / two
commits after. This isolates advisory history cost, not overall compiler throughput.
The throughput view now reports completed history transactions and average compiler
slot utilization across the run; instantaneous active slots remain a separate count.

### Polishing rename reads and retryable metadata contention

Renaming scans only matched function IDs, names, units, addresses and exact saved C,
then loads complete judge inputs for affected functions. All effective current headers
are resolved in one snapshot. The existing header/caller/new-name capture checks and
exact regression judge still determine whether a rename can be applied. A synthetic
600-function scan decoded 600 packed text values instead of 2,400; this measures scan
work only, excluding affected-body compilation and source updates. It does not prove
that observed multi-minute rename calls have been eliminated.

Single-flight metadata waits have a distinct retryable exception. Map requests return
503 with Retry-After; MCP tools preserve the actionable error rather than reporting an
unexpected tool crash. Wait limits and active builders remain unchanged, and a builder's
own timeout is still a failure. HTTP diagnostics now record even fast 5xx responses
with status, route template and elapsed time, without headers, bodies or query values.
Responses generated by an external tunnel or provider outside the local ASGI app
still require that component's logs or a recorded agent exchange.


Saved verification releases its global admission lane once the saved judge and its
worker publications finish, before rendering/exporting verified progress. The project
lock, frozen input handling, dirty acknowledgements and guarded file publication remain
in place. A bounded two-project regression stalls export, admits the next project's
verification, then completes or fails the first export while checking that cleanup
cannot release or clear the next owner's lane. This removes export duration from shared
admission occupancy; it does not remove pre-judge comparison time or same-project waits.

### Preserve cancellation through compiler cleanup

A denied process-group signal must not replace a search's own time-limit or
cancellation exception. Compiler cleanup now attempts direct-child termination,
closes both pipes and removes process tracking even when group signalling fails.
The cleanup denial remains a warning with PID and errno; when cleanup is the only
failure, it still fails the compiler invocation instead of returning a candidate.
The operating-system reason for a denied group signal is not inferred from this fix.

The dedicated permuter's stderr log now includes timestamps, PID and startup source
revision, including uncaught exceptions. Its stdout remains the JSON control
protocol. Development worker revision checks also cover shared cache, network,
compiler and progress contracts: lazily importing a new view against a cached old
read-cache module previously caused an ImportError after source updates.

### Narrow SDK discovery to this game's fingerprints

Shared-code discovery now uses the catalog's `(arch, big_endian, fingerprint)` index
before fetching SDK signature rows. Only fingerprints present in eligible destination
targets are read; an empty target set does not scan SDK signatures. Library selection,
size checks, hint limits and destination-judge requirements remain unchanged. SDK
signatures continue to supply name-only hints, not verified C or match credit.

A synthetic catalog with 5,002 signatures and two relevant entries fetched 5,002 rows
before and two after, with identical matching rows. Across three paired trials,
median idle query time was 4.26 ms versus 0.159 ms; with a competing Python CPU thread,
1.927 s versus 20.5 ms. These measure the discovery query component, not live app or
full cross-game pass latency. They do not establish that every long synchronization
or source-fetch wait has been resolved.

### Current function reads during saved verification

`get_function` summary, brief, candidate and supporting current-state views use the
foreground read lane once the imported context and saved evidence are committed.
They retain the saved-file signature guard and wait while evidence is being installed.
Recovery/experiment views still wait for the complete recheck. A read does not create a
verdict, and the compiler's ordinary revision and byte checks still apply afterward.
`progress_recheck_wait_ms` measures the condition wait separately from file validation,
SQL, shared verification admission and compiler time.

Cross-project donor discovery transfers scalar metadata in one JSON row per compatible
project. Bodies are read only for selected candidates, with their usual checksum checks.
The persistent twin index tests source presence in the physical function table; its
metadata count/reindex queries must not invoke the compressed-source detail view.

Bounded synthetic checks (2,001 donor rows, three trials) reduced native row transfers
from 2,001 to one. Median query time was 3.79 to 1.33 ms idle and 517.33 to 14.10 ms with
one competing Python thread bounded to two seconds. An actual Store fixture with 513
compressed bodies reduced index-count payload callbacks from 513 to zero, with identical
count/latest values (7.17 to 0.047 ms median). These are component measurements, not
end-to-end runtime speedups. Regressions keep a saved compiler future unresolved while
reading current function data, then complete the same ordinary saved judgment afterward.
