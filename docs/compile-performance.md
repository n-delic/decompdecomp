# Compiler throughput

Deep searches, background permutation/repair turns and compiler comparisons use private
compile sessions. Imported projects still use their own build system; the compiler staging
and object cache described here applies to the app's single-function compiler pipeline.
Prepared scoring reaches every architecture supported by `scoring.diff`.

## What is reused

- One private workspace per search worker. Exact include bytes are read on every request;
  unchanged normalized headers are retained instead of recopied. Other outputs are removed
  before the next request. Changed/missing headers invalidate preparation, and compiler-written
  headers are restored. Case-colliding trees and directory symlinks use ordinary private staging.
- Successful compile objects, scoped to that worker's session: at most 128 entries / 32 MiB,
  with a 4 MiB maximum per entry. Keys include source, staged headers, prelude/intrinsics,
  compiler configuration and invocation identity, environment, and absolute command file
  metadata. Returned results are independent copies. This cache never stores a judge verdict.
- ROM target objects and parsed target inputs (16 bounded entries), keyed by exact function
  inputs/configuration and, for symbolic objects, symbol-table identity and generation.
- Target instruction decoding and fingerprints (8 entries, targets up to 64 KiB), keyed by
  bytes, relocations, processor, byte order and name. Candidate decoding remains fresh.

Nested sessions share preparation only on their owning thread. Other threads receive independent
state, and all private directories/caches are released at the end of the search, including errors.
User-controlled compiler commands without coordinator identity retain ordinary staging.

Cancellation is checked before cache lookup and after compilation. Failures and timeouts are
not cached. Artifact capture uses a fresh invocation/directory. Time/path macros, line-spliced
forms, token pasting, digraphs/trigraphs and dynamic include probes disable object reuse.

## Scoring and verification

Each permutation candidate gets one primary scoring diff. Search feedback still receives source
locations and physical register-cycle rows when applicable. Export/address reports are deferred
for ordinary misses; baselines, improvements and exact candidates receive normal diagnostics.

The ROM linker still checks linked code, data and relocations on every evaluated object. Saving
an improvement/match still uses normal publication/input guards. Imported whole-image judging
is unchanged. Object reuse does not make a cached score or previous match authoritative.

## Measure and tune

`compile_timing.snapshot()` exposes numeric totals for requests, cache hits, setup, queue wait,
compiler execution, linking, primary scoring and search diagnostics. Durations are seconds;
parallel durations are accumulated thread time, not wall time. With `DECOMP_PERF=1`, each search
session writes its own numeric summary to the application log, without source/paths/headers.

Run a synthetic throughput comparison with an **already installed pinned** compiler:

```sh
PYTHONPATH=src python tools/benchmark_compiles.py --toolchain gcc-2.7.2-kmc --count 96 --workers 1,2,4,8
```

This uses scratch directories and synthetic C, never opens a project database and never installs
tools. The unprepared control also supports cancellation. Unique candidates are the default;
`--repeat-every 4` measures duplicate candidates separately. Run several times on an otherwise
idle computer: the reported recommendation is only the fastest worker count tested in that run.

Set `DECOMP_COMPILER_THREADS=8` before launching to cap simultaneous compiler invocations globally.
Zero/unset uses the existing CPU-based default; valid explicit limits are bounded to 1–256.
Background work retains a smaller admission gate to leave interactive capacity when the cap
is greater than one. This cap is distinct from function-search threads and project worker limits.
The benchmark reports queue wait so excessive search concurrency is visible. No startup benchmark
or automatic change to the user's chosen concurrency is performed.

## Measurements during this change

On an Apple Silicon Mac, installed KMC GCC 2.7.2, 48 tiny unique candidates, eight workers were
fastest among 1/2/4/8 in two runs. Unique-candidate throughput varied roughly 6–31 candidates/sec
with worker count/load; header preparation alone showed no consistent gain for this tiny header.
The compiler dominated execution time. These are synthetic results, not a game-search speedup.

For a separate 130-instruction synthetic MIPS scoring loop (400 comparisons), prepared target
instructions increased throughput from approximately 507 to 707 scores/sec with the same score.
Regression tests also enforce one primary scoring diff per candidate and exact input invalidation.

A separate duplicate-candidate run (32 requests cycling four sources, one worker) executed four
compilers and served 28 scoped hits: 5.63 seconds without preparation/cache versus 0.82 seconds
with it. Actual benefit depends on duplicate rate; unique candidates cannot gain this speedup.
