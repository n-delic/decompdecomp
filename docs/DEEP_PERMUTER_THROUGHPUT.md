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

## CPU and Metal candidate ranking

The benchmark can replay the same proposals in their original order or rank them
before compilation. This fixture uses a synthetic KMC MIPS function that adds a
constant. The ranker extracts the target's constant from its `lui`/`ori` pair and
scores each candidate by bit distance from that constant. The replay plants the
exact constant once per function at a seeded position in the original order.
Both backends compute the same scores, keep original order on ties and send the
results through the normal ROM candidate stream and judge. The replay stops each
function at the first verified match.

On an Apple M5 Max, three unprofiled full-search runs with 16 functions, 32
variants, eight workers and eight compiler slots took 6.31–8.69 seconds at
54–58 compiler invocations per second. This unrestricted generator found zero
matches on the fixture. The first concurrent run hit a lazy import error before
measuring throughput, so the benchmark loads the MIPS runtime alias before
starting workers. Asynchronous repair-outcome analysis is disabled for these
benchmark commands.

The controlled replay used eight functions, 32 candidates each, and four workers
and compiler slots. Each order verified eight exact matches on each of three
runs. Times below are medians across those runs. Time to match is the median
across functions in each run, measured from the start of the batch so it includes
queueing and ranker startup.

| Order | Time to match | Wall time for all eight | Judged candidates | Compiler invocations |
| --- | ---: | ---: | ---: | ---: |
| Original proposal order | 1.07 s | 1.35 s | 118 | 123–128 |
| Swift CPU | 0.43 s | 0.48 s | 8 | 24 |
| Metal | 0.61 s | 0.65 s | 8 | 21–24 |

The compiler count includes speculative work already started when a match stopped
the stream. For 256 candidates, CPU scoring took about 2 µs. The GPU kernel took
about 10 µs, and buffer setup, dispatch, waiting and readback took 3–5 ms
combined. The one-shot helper, JSON exchange, reference-score verification and
sorting together took about 8 ms on CPU and 112 ms on Metal. A persistent helper
could amortize startup, but the CPU already computes this feature cheaply.
The planted matches make this a test of dispatch cost and the value of knowing
which candidate to try first. It does not measure discovery on real near-misses
or modified candidate ordering in the full search.

On a Mac with the pinned compiler and a scratch `TMPDIR` and `DECOMP_HOME`:

```sh
swiftc -O tools/deep_candidate_metal.swift -o "$TMPDIR/deep-candidate-rank"
PYTHONPATH=src uv run --locked python tools/benchmark_deep_pipeline.py \
  --full-search --functions 16 --variants 32 --workers 8 --slots 8
for backend in current cpu metal; do
  PYTHONPATH=src uv run --locked python tools/benchmark_deep_pipeline.py \
    --functions 8 --variants 32 --workers 4 --slots 4 \
    --replay-order "$backend" --ranker "$TMPDIR/deep-candidate-rank"
done
```

## Reading a live project pass

The throughput line under **Deep Permuter → Activity** reports active compiler
slots and waiters across the pass's workers. Each worker reports a snapshot about
twice a second, so the active count can miss short compiles. The average slot
utilization includes short compiles through accumulated compiler time. The slot
count reflects the allocations to worker processes, which can differ from the
number entered in settings when the app divides capacity among those processes.
Work elsewhere in the app is outside the pass count.

When CPU constant ranking is enabled and has scored a batch, the same line shows
total thread CPU time spent extracting C literals, the number of ranked batches
and the average CPU milliseconds per batch. The total includes parent extraction
on cache misses across all workers. It excludes sorting, distance calculations
and time spent waiting for a CPU. A low active count with few compiler waiters
suggests the compiler queue has room for more work.

### Rust extraction experiment

On an Apple M5 Max, an optimized Rust byte scanner called in-process through
`ctypes` processed batches of eight ASCII candidate texts in a median of
0.023 ms versus 0.673 ms for the current Python extractor, including FFI and
result conversion. The inputs were 128 saved, open-function bodies from a local
MIPS project with synthetic edits. The prototype matched Python's results on
all 1,024 candidate texts and on 1,000 generated token edge cases. This measured
literal extraction only.

Packaged builds now use the Rust scanner for eligible ASCII candidate source.
With the shipped scanner's validation and result conversion, the same 128-body
test measured 0.030 ms per eight candidates versus 0.699 ms in Python.
On the 12 bodies with supported target constants, the entire ranking step
took 0.041 ms versus 1.051 ms per batch. These timings exclude candidate
generation, compilation and judging. Literal sets and the stable candidate order
are checked against Python. A missing native library or symbol, non-ASCII source,
unterminated comments or strings, and source larger than 256 KiB use Python.

For a source checkout, run `uv run --locked python packaging/build_alignment.py`
with Rust installed. Restart after building the library. To compare against
Python with the same ranking option enabled, start the app with
`DECOMP_NATIVE_RANK_LITERALS=0`.

## Candidate source checks

A compile session reuses source-shape and call-arity results between admission
and judging. The bounded caches use exact source text, function spelling and
header as applicable. The Rust scanner counts nested compound updates and
empty constant `do` loops for supported ASCII inputs. Non-ASCII text, raw
strings, unterminated comments or strings, oversized inputs and unavailable
native libraries use the Python checks. Call-arity analysis still uses the
existing Python implementation.

On an Apple M5 Max, the isolated source-shape scan took 5.2 µs in Rust versus
230 µs in Python at the median for a synthetic body with 80 neutral statement
pairs. The compile-path benchmark produced these results across five runs per
configuration, alternating configurations with four workers and four slots.

| Candidate checks | Median compiler invocations/s | Range |
| --- | ---: | ---: |
| Before the change | 92.3 | 79.4–103.0 |
| Session caches with Python scanner | 94.7 | 75.0–100.5 |
| Session caches with Rust scanner | 94.8 | 84.9–114.5 |

Each run made 192 compiler invocations, wrote 192 history rows, and produced
the same score digest. Run-to-run variation was larger than the change in
median throughput. The benchmark uses fixed proposals through compile
admission and the real KMC compiler and judge. It excludes candidate generation
and does not establish a match-rate gain on real projects.

With the pinned compiler installed and scratch data and temporary directories,
build the native library first and then run:

```sh
uv run --locked python packaging/build_alignment.py
PYTHONPATH=src uv run --locked python tools/benchmark_deep_pipeline.py \
  --functions 12 --variants 16 --workers 4 --slots 4 --declarations 600 --source-statements 80
```

Set `DECOMP_NATIVE_CANDIDATE_CHECKS=0` for the cache-only run. For the baseline,
use the same benchmark fixture with the application code from before this change.

## Where the processor goes, in the running app

The app and each helper process it starts (the proposal workers that run the decomp-repair library, the permuter
worker) sample their own threads twice a second (`thread_cpu.py`; under 1% of a core) and charge the kernel's CPU time to
the code each thread is in. Reports are `<home>/.decomp/logs/thread-cpu*.log` (and a `.json` twin), rewritten every
minute; code inside decomprepair and decompassets is named by module. To add every process up:

```bash
uv run python -m decompnet.thread_cpu            # this edition's home; or pass a logs folder
```

Compiler processes are not Python: their time is in `compile_timing` (the run's `throughput`). `DECOMP_THREAD_CPU=0`
turns all of it off. Helper reports older than a day are removed when a helper starts.

## N64 local batch judging

When the local random-rewrite lane compiles several candidates together, the Rust gateway can link their
objects against one project snapshot and reuse the disassembled target for summary scoring. The search
consumes a batch summary only for a miss at or above its current best score and while the project and
image inputs still match. Improvements and exact matches go through the ordinary judge and an independent
compile before they can be published.

With a current gateway and KMC GCC installed, compare the ordinary individual Rust requests and batch
requests by toggling `DECOMP_N64_BATCH_JUDGE` for the same command:

```sh
DECOMP_N64_BATCH_JUDGE=0 PYTHONPATH=src uv run --locked python tools/benchmark_deep_pipeline.py \
  --local-batches --functions 16 --variants 32 --workers 4 --slots 4 --declarations 300
DECOMP_N64_BATCH_JUDGE=1 PYTHONPATH=src uv run --locked python tools/benchmark_deep_pipeline.py \
  --local-batches --functions 16 --variants 32 --workers 4 --slots 4 --declarations 300
```

On an Apple Silicon Mac, the median of three runs rose from 295 to 338 judged candidates per second
(about 15%). Both modes ran 64 compiler invocations, recorded 512 trials, and returned the same score digest.
The batch mode consumed all 512 batch summaries. This controlled workload includes compilation and trial
history. The speedup depends on how often a real search can compile candidates together.
