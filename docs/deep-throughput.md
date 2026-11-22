# Deep-permuter throughput

## Concurrent compilation, ordered judging

A deep function search lazily starts one shared pool of up to four compiler workers,
limited further by `DECOMP_COMPILER_THREADS`. These workers compile independent random
variants from an eight-candidate chunk. Each worker keeps a private compiler session
and workspace for the function search. The existing compiler semaphore, background
slot reservation, resource limits and cancellation checks still apply.

The search lane consumes results in generation order, then links, judges and publishes
through the ordinary pipeline. Workers cannot claim functions or publish results. A
bounded admission window retains at most eight admitted candidates across lanes; a
whole window is admitted atomically to avoid partial-window deadlocks. Pending compiler
work is canceled when a better parent supersedes it. Already-started work consumes the
lane's existing allowance even when its result is discarded; canceled queued work does
not. This avoids silently multiplying the user's budget through speculative work.

Only ROM compilers with captured, pinned inputs use speculative compilation. Imported
builds can mutate a shared build tree, and custom/external include inputs can change
without database revisions. Those retain their ordinary serial build path. Multiple
profiles per candidate also keep their current lane path rather than speculating work
that might exceed their profile allowance. Independent configured search lanes remain
available for those cases.

## Sharing across lanes

All lanes of one function search share a bounded successful-object group. Keys still
come from the compiler's full input capture: source, staged headers, prelude, command,
configuration, environment and tool identity. Simultaneous duplicates coalesce before
execution, and later duplicates can reuse completed successful objects. Artifacts,
volatile inputs and uncaptured dependencies retain their existing exclusions. The group
holds at most 128 objects / 32 MiB, with a 4 MiB per-object ceiling; failures are not kept.
Returned result objects are independent. Cancellation of a follower cannot cancel the
owner; a canceled owner leaves surviving followers free to retry.

The preparation owner already serializes source preparation and matching preflight.
Now only that lane performs warning preflight too. Non-exact baseline scores and detailed
evidence are shared after checking the full captured revision/source/compiler/rules
identity. Changed inputs and exact results use the ordinary fresh path. Baseline work is
charged to its owning lane; reused evidence does not spend another compiler allowance.

Compiled-ahead object handoff checks actual compile snapshots and environment/tool
identity. An unrelated function's score update need not discard an otherwise valid
object. Header/command/source/identity changes reject it. Linking, source-specific
winner diagnostics and the existing fresh publication guard remain authoritative.

## Less Python work on ordinary misses

Ordinary non-improving random trials keep scores, instruction rows and output
fingerprints, but defer source-line mapping, physical allocation diagnostics and detailed
agent-facing alternative evidence. Baselines, targeted local repairs, improved candidates
and exact matches still collect that evidence immediately. Random input-effect records
use a whole-source replacement span instead of computing a line alignment; all outcome
counts remain intact. Targeted trials retain exact changed spans.

Family ranking computes allocation diagnosis once and passes it into the final ordering
step. Parent evidence is loaded lazily, avoiding Python's eager evaluation of a `getattr`
default even when the parent rows are already available. These changes reduce work on
the search thread. Source generation already runs in a persistent child process, and
compiler execution dominates the measured synthetic workload; another process boundary
for the reduced metadata path is not justified by that measurement.

## Verification and measurements

Regression tests cover actual overlap with ordered consumption, bounded admission across
lanes, speculative-work accounting, cancellation, changed-input rejection, unrelated
revision changes, group cache bounds/invalidation, canceled-owner recovery, baseline
sharing, and immediate detailed diagnostics for every winner and baseline.

On this Mac, the installed KMC compiler benchmark with 24 unique synthetic C candidates
measured 6.76 seconds / 3.55 candidates per second with one prepared worker versus
2.03 seconds / 11.81 candidates per second with four. A separate scratch test exercising
the new queue and private sessions measured 9.50 seconds with one worker versus 4.55
seconds with four while regression tests were also running. These are compile-stage
measurements, not a claim that every complete deep search is 2–3 times faster. Generation,
linking, diagnostics, function size, compiler runtime and other active work affect the
complete result. No user game or database was used or modified.

## Measured on a 12-lane maximum-throughput pass (18 cores)

Six minutes in: 61,639 compiles (165 a second), but the 24 compile slots were 15% busy and each lane process used
30-60% of a core. The search waited on its own serial loop, not on the machine. Per compile, summed over threads:
compiler 21.6 ms, local proposals 5.4 ms, history writes 3.2 ms (10,642 transactions of 18.7 ms through the single
writer), link 1.6 ms, setup 0.8 ms; `DirEntry.stat` from header staging was 8-12% of a lane's CPU samples.

What changed:
- **Headers** are re-read and re-staged at most every `compile_sessions.RECHECK_SECONDS` (0.5 s) instead of for
  every candidate. 300 headers: 9.0 ms to 0.04 ms per compile; 19 headers: 0.84 to 0.33 ms. Each result is keyed by
  the exact bytes it was built with, so an edit only reaches candidates half a second later.
- **History batches** hold up to 64 MiB / 8,192 statements / 10 s in a maximum pass (8 MiB / 512 / 2 s otherwise),
  so a lane commits a few times a minute instead of several times a second. They are advisory counters, and a
  crash loses at most that window of them.
- **Parallelism**: a lane's lookahead window starts at three and grows to eight (it was two to four, and a lane
  had only two compile slots, so deeper windows could not help). With no slot count set, a maximum pass gives each
  lane `max(4, ceil(4 * cores / lanes))` slots, sizes the pool at two slots per core and, with no function count
  set, runs one and a half lanes per core. A number set in the form is still respected.

## SQLite readers in a lane

Sampling a running 32-lane pass (8.5 CPU-seconds of one lane in 10 s) put SQLite at 17% of the lane, and under it
`sqlite3Init` / `sqlite3InitOne` (parsing the whole database schema) at about a fifth of all lane CPU. A new reading
connection parses the schema before its first query: 2.2 ms for a new reader against 0.003 ms for the same query on
a kept one. `Store` kept eight idle readers and closed the rest, and a lane with many compile threads went past
eight at once all the time, so it opened and closed connections continuously. It now keeps 64 (`READERS_KEPT`) and
every connection keeps 512 prepared statements (`STATEMENT_CACHE`, Python's default is 128).

## Tried and not adopted

- **RAM disk with `.fseventsd/no_log` for compiler scratch (macOS).** `fseventsd` used 168% CPU on the running pass
  and the idea was that a volume that is not logged and not indexed would remove it. Measured with six processes
  creating and deleting files for 8 s: `fseventsd` used the same CPU on the RAM disk as on an ordinary folder, even after
  remounting with the marker in place, and still delivered events to a watcher. `no_log` stops the history it writes,
  not the work of receiving the events.
- **Piping cpp, cc1 and as into each other** (no `func.i` / `func.s`, the three programs running together). The object
  is byte-identical (KMC GCC 2.7.2) and wall time per compile is lower on an idle machine, but the one-command form
  needs `bash`, whose start-up cost 4.5 ms of CPU per compile: 12.4 ms of child CPU per compile against 7.9 ms, and the
  machine was out of CPU, not wall time. A pipeline started from Python without a shell would avoid that but has to
  carry the watchdog, process tracking and cancellation for three processes.

## Finding where a pass's CPU goes

Every helper process samples its own threads' kernel CPU every two seconds and charges it to the code the thread is in
(thread_cpu.py: logs/thread-cpu-<role>-<pid>.log and .json). The deep-search lanes, the heaviest Python of a pass, were
the one helper not named in it; they now write `thread-cpu-lane-<pid>`. Add every process up with

    python -m decompnet.thread_cpu <home>/.decomp/logs --minutes 30

`DECOMP_THREAD_CPU_STACKS=3` (set before starting the app; its helpers inherit it) names the three callers of each
location, `app/x.py:f < app/y.py:g < app/z.py:h`, to read the report as call paths. `DECOMP_THREAD_CPU=0` turns the
sampler off. It reads kernel CPU time, so a thread waiting on a lock or a pipe is not charged: only code that
used the processor appears.
