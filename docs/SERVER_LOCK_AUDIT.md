# Server responsiveness under contention

The November 2026 live-process sample showed the ASGI main thread waiting for a Python lock while background workers continued. Two independent health requests timed out. Native sampling could not identify the precise Python lock owner; attaching a Python stack reader required unavailable elevated access. This audit fixes the independently reproducible blocking paths rather than assuming one lock explains every incident.

## Audited boundaries

| Area | Blocking risk | Treatment |
| --- | --- | --- |
| Repository setup, fork, push and sync | Job reservations acquired a thread RLock on the event loop; sync validation could enter progress import and database locks | Whole reservations run on one control worker, retaining atomic duplicate-job checks. Validation reads a progress snapshot; the job performs synchronization. |
| Import, restore, SDK/tool/agent installs and fork setup | `Jobs.start`, admission, logging and executor setup could wait synchronously | Job admission runs on reserved control workers. Actual work keeps its own job thread and progress/cancellation. |
| Repair replay/recovery | Duplicate checks and job creation held the job lock on the event loop | Each atomic reservation stays on a single worker. |
| Job cancellation and settings job discovery | Used the same generic worker capacity as long operations, or acquired a lock directly | Independent control capacity, separate from builds and UI snapshots. Large job JSON is encoded off the event loop. |
| Progress activity | Even an in-memory status call acquired the activity mutex | Activity status is read on a worker. Project GET routes retain existing snapshot-only contexts. |
| Browser bridge and login/OAuth | Capability/session locks and settings file locks could block HTTP dispatch | Locking operations are offloaded. Cookie authorization reads the current bounded session map without waiting for a writer, and still checks expiry. OAuth state consumption remains atomic. |
| Agent refresh, prompt previews, tool removal, background task admission | Synchronous helper calls could reach mutexes, subprocess or disk work | Offload at the request boundary. |
| Database/process writer gates | Waiting can be legitimate during transactions and must not be bypassed | Preserve writer ordering, transaction scopes, rollback and verification. Keep these operations off the event loop; a regression holds a real writer while serving health. |
| Read caches and map snapshots | Cache builders/consumers can wait for another worker; recursive snapshot waits could wait on themselves forever | Existing transaction cache bypass remains. Snapshot consumers have a bounded wait; recursive builders fail immediately. Successful builders remain valid after a consumer timeout. |
| Diagnostics | The stall logger itself could wait for a logging lock on the event loop; a completely blocked loop could not report itself | Log from workers. An independent heartbeat watchdog captures bounded thread code locations during a persistent stall. |
| Room host and MCP dispatch | Database, claim, compiler and member/auth operations | Checked the existing worker boundaries; synchronous room business logic and MCP tool/auth/discovery work already run on workers. Shared UI room admission now uses the control workers too. |
| Shutdown | Waiting for open requests can precede cleanup | Existing immediate signal-driven bounded shutdown remains. Do not force-release locks or commit a half-finished transaction. |

## Diagnostics and regression gates

A development or packaged runtime writes `.decomp/logs/event-loop-stacks.log` if its event loop misses heartbeats for five seconds. One bounded report is replaced at most every 30 seconds. It contains thread names and Python file/function/line locations, not locals, arguments, source lines, SQL bindings or request URLs. The watchdog stops with the server lifespan.

Tests use one shared ASGI event loop, deliberately hold backend admission, job reservation, progress and database writer paths, and require concurrent `/health` responses. The database test checks that the delayed settings write commits and SQLite integrity remains valid. Separate tests cover reserved capacity, authorization expiry, recursive/bounded cache waits, watchdog capture and an AST gate against direct blocking lock/job/database admissions in asynchronous routes.

Hardware exhaustion, the interpreter or a native extension can still prevent scheduling. This does not promise that every possible future hang is impossible. These boundaries prevent the reproduced backend lock waits from freezing the server and make another stall diagnosable without an elevated debugger.

### Follow-up: indirect waits

Lazy project imports in async route bodies now run through `App.imports` on a worker. Python's import machinery can wait on another thread initializing that module, even when the importing statement looks harmless. The helper preserves from-list submodule loading and alias behavior. A regression holds an actual module import lock and checks concurrent event-loop scheduling; a structural gate covers future project imports in async routes.

PATH discovery, MIME database initialization, old-install detection and local path expansion in async import/join/tool installation handlers also run on workers. Prompt defaults can lazily import tools and are offloaded. Diagnostics import their dependencies at startup and start/join the watchdog on workers. Existing transaction, claim and repository locks remain intact.

### Worker lock ordering and cache waits

A further audit found schema initialization taking an initializer mutex before the database writer, while callers may already own that writer. This creates a cycle between concurrent first use and initialization inside a transaction. Session history, deep-permuter history, shared knowledge, repair-input evidence and proposal cursors now use the existing database writer to serialize initialization. Readiness is published after the outer transaction commits, never after a nested transaction that can still roll back. Lazy shared-knowledge creation also acquires writer admission before the connection mutex.

Shared `ReadCache` consumers and incremental-map consumers previously waited indefinitely for their builders. They now wait at most ten seconds; recursive same-entry read builders fail immediately. A timeout does not cancel a builder or publish stale data. Cross-key cache cycles unwind instead of pinning readers permanently. Tests reproduce an initializer/writer inversion on the previous version, check rollback and all five initializer paths, and cover recursive, stalled and cyclic cache builds and map contention.

Compilation-sharing followers also reject self-waits and have a five-minute maximum wait. Search-scoped followers continue checking cancellation every 50 ms. Timing out a follower does not cancel the owner, share its result object, bypass compiler admission or publish a verdict. Separate tests check normal sharing, independent results, recursion and bounded waits for both global and search-scoped sharing.
