# Measuring deep search throughput

Compiler slots are a limit, not a promise of CPU utilization. A deep search must also
prepare candidates, check previous trials, run the judge and write history. Increasing
functions or slots can add interpreter and database contention instead of increasing
compiler invocations. Start with automatic concurrency and measure compiles per second.

The candidate path reads the current database without synchronizing the saved project
for every candidate. The active-project watcher imports contributions and exports saved
work. The judge still checks progress revisions and active saved-file rechecks before
recording a result, and ROM candidates still pass the ordinary linker at their real
addresses. Imported-project candidates still pass their ordinary project judge.

Plain-call preflight caches up to four exact shared headers of at most one million
characters each. Source declarations and shadowing are checked separately for each
candidate. Incomplete header fragments retain the combined header/source scan;
macros, C++ and ambiguous declarations continue to defer to the compiler.

## Reproduce a compile-path benchmark

Use an isolated environment, `DECOMP_HOME` and `TMPDIR` on a scratch volume. With the
pinned `gcc-2.7.2-kmc` compiler already installed:

```sh
PYTHONPATH=src python tools/benchmark_deep_pipeline.py \
  --functions 200 --variants 2 --workers 200 --slots 200 --declarations 3000
```

Repeat with automatic-sized worker and slot counts for the machine, for example 16.
Run before/after commands sequentially with the same arguments. The tool never opens a
user project or downloads tools. It creates synthetic targets and uses real candidate
preparation, compiler invocations, ROM linking, scoring and durable miss history.
Verify equal compiler invocation counts, history row counts and `scores_sha256`.

This benchmark excludes repair generation, initial project import, GUI serialization
and a real saved-project file tree. It isolates the candidate-to-judge path; its speedup
is not a prediction for every game or Mac. Phase times sum concurrent workers and must
not be added together as wall time. App CPU excludes compiler and generator processes.

## Full search and admission waits

Add `--full-search` to exercise the normal deep-search entry point, including source
preparation, warning/flag preflight, baseline checks and proposal generation. Add
`--balanced` to compare the balanced preset. `--profile` writes a cProfile file for
the first full-search function beside the scratch directory. Keep TMPDIR on the
scratch volume. GUI rendering and a real saved-project file tree are still excluded.

For full searches, concurrent speculative work and object-sharing cache hits can
slightly change actual compiler invocation counts. Compare completed search results,
score digests and history rows too, and report any invocation-count difference.

History priming and compile submission each share advisory fingerprints only while
preparing one bounded batch. That context ends before judging; compiler identity,
header, target, environment and revision guards are captured afresh on the actual
candidate. Large lexical stability checks cache at most four byte strings, each
4 KiB–2 MiB, keyed by exact bytes and the complete staged include-name set. They
never cache file contents by pathname or supply a match verdict.

The scorecard's active count now means requests that acquired every compiler gate.
The waiting breakdown distinguishes this search's limit, the balanced-background
reserve and the app-wide shared compiler limit. Shared busy slots include other
app work, so an idle deep-search slot alone does not prove the machine is idle.
Cumulative wait time sums threads; use the current waiter counts to locate a live
stall. A partly checked project queue is intentional bounded lookahead, not an
attempt to prepare every function before any search can run.

Repair implementation fingerprints are captured once per search session and refreshed
for the next session. Python does not reload those implementations mid-search. This
is advisory search-version evidence only; compiler, header, target, environment and
revision checks remain fresh for actual candidates. Outside sessions, implementation
fingerprints remain fresh on each call.

## Concurrent macOS compiler overhead

Compiler metadata lookups construct only the requested toolchain instead of rebuilding
objects for the entire catalogue. The catalogue listing remains available for Setup;
individual lookups still return fresh metadata and local SDK copies remain dynamic.

Native macOS compiler watchdogs share a system process snapshot for at most 100 ms,
measured from the start of the scan. Each watchdog still walks its own descendant tree,
checks its own memory limit and timeout, and stops only its owned process group. The
250 ms watchdog interval and limits are unchanged. This avoids concurrent watchdogs
spawning duplicate system-wide `ps` scans; failed scans are retried after at most 100 ms and a slow
scan is not retained as fresh. No compiler results or judge decisions are reused here.

To compare this path, use the same benchmark script on both revisions:

```sh
PYTHONPATH=src python tools/benchmark_deep_pipeline.py --full-search \
  --functions 32 --workers 32 --slots 32 --variants 32 --search-seconds 180
```

The synthetic saved source starts at a conservative score rather than a fabricated
near-match. Otherwise reloading that row could overwrite the measured baseline and
make the reported best-score digest depend on scheduling. This fixture correction
must also be applied to the baseline checkout when comparing revisions.
