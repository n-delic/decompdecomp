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
