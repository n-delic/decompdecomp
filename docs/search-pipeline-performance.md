# Search pipeline performance

Random permutation searches now consume eight variants at a time from the existing
persistent generator process. The generator flushes each JSON reply; its existing
pipe reader retains at most 32 replies, allowing generation and compilation to overlap
without creating another worker pool. The operating system pipe provides additional
backpressure. Compiler concurrency limits are unchanged.

An improved parent discards its remaining variants. An incompletely consumed generator
is terminated and the next round starts a new one, so replies cannot leak across
parents. A completely consumed generator stays alive. Stop/deadline checks run while
waiting for replies; failed generators still fall back to the simple rewrites.

Deep searches prime persistent miss feedback with one query per chunk. For pinned ROM
compilers with captured inputs, the scope calculation is also reused while the exact
input revision, compiler identity, function/project state, repair rules and environment
remain unchanged. Pending writes take precedence over primed misses. Imported builds,
custom compilers and external includes retain fresh per-candidate dependency scans and
individual lookups: their files can change outside database revisions. Extending batch
preparation to those builds needs a filesystem dependency revision mechanism.

Within a search session, identical target/candidate instruction inputs reuse advisory
non-exact comparison results. Keys include raw bytes, relocations, processor variant,
endianness, architecture and function name. The cache holds at most 128 comparisons,
with an 8 MiB serialized payload budget and a 1 MiB per-entry ceiling; functions whose
combined code exceeds 32 KiB bypass it. Returned row lists are independent. Exact
comparisons are not retained. Actual-object linking, strict byte verification,
source-specific locations, winner publication and concurrent-input guards still run.
This is an instruction-comparison cache, not an authoritative match cache.

## Timings

`DECOMP_PERF=1` and `tools/benchmark_compiles.py` include numeric counters for:

- `comparison_hits`: advisory comparisons reused.
- `driver_native`, `driver_wsl`, `driver_linux-vm`: the complete driver invocation in
  each execution environment, including its runtime/sandbox overhead.
- `runtime_setup`, `process_launch`: native wrapper construction and process creation.
- `assembly_preprocess`, `assembly_execute`: separately invoked stages of explicit
  assembly verification.

These are nested measurements, not additive phases. Compiler drivers that invoke
an assembler internally remain one opaque invocation; these counters do not claim to
separate compiler, assembler and Wine/VM execution time inside that driver. No extra
compiler invocations or altered flags are introduced for measurement.

A scratch synthetic test with 300 repeated 120-instruction MIPS comparisons took
0.352 seconds without a session versus 0.029 seconds with comparison reuse before
further reducing row-copy overhead. This measures repeated advisory comparisons only,
not compile throughput or an end-to-end search speedup. Unique outputs still need
fresh comparisons. Regression tests cover invalidation, exact results, bounded caches,
streaming before generation completes, cancellation, obsolete streams, generator reuse,
error fallback and pending-write precedence.
