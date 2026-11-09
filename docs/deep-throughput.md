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
