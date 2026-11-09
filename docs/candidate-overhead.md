# Reducing candidate overhead

The candidate pipeline reuses read-only work and groups advisory writes. Compiler execution,
linked-byte checks, objdiff verdicts, claim rules and publication/input guards remain unchanged.

## Fingerprints

A thread-owned candidate scope covers deep/current-profile trials, ordinary permutation scoring,
ROM agent attempts, compiler-profile probes and quiet automatic attempts. Within that scope:

- Repeated ROM fingerprints reuse the same result only for an identical project/function snapshot
  and current database `inputs` revision. Header, symbol, matched-helper and configuration edits
  invalidate the snapshot. Project reads and repair-implementation digests are shared within the
  trial as well. Nothing is retained as a judge verdict.
- Imported projects still read external build dependencies on every scope request; these files
  can change without a database revision. Explicit scheduling snapshots retain their own rules.

There are at most 32 memo entries. The scope ends on success, error or cancellation and is not
inherited by another worker thread.

## Object readers

Linking, function extraction, debug-line lookup and export diagnostics share a read-only ELF reader
for identical bytes during a candidate. Symbol discovery is reused too, with independent dictionaries
returned to callers. Name-normalized C++ bytes have their own entry. No linker placements, scores or
verdicts are cached. The cap is four readers / 16 MiB of input bytes; larger inputs use normal parsing.
Non-ELF readers and unsupported formats retain existing behavior.

## Bookkeeping

Background repair/permutation turns and deep-search workers can group miss-only policy counts,
input-effect measurements and non-winning reuse history. Each worker has its own queue. It flushes:

- at 64 SQL statements or 1 MiB of queued statement data;
- at the next submission after 0.5 seconds (an in-flight compile can cross this boundary);
- before a policy read on that worker, and before a verified candidate is recorded;
- immediately for improvement/match statistics, and on completion, cancellation or failure.

Queued reuse feedback is visible to the owning worker, so duplicates in imported projects still skip
compilation before the batch reaches disk. Successful game work is never queued. An abrupt process
kill can lose only the pending advisory counts/miss cache; normal shutdown flushes them. If an error
is already propagating, a statistics-flush failure is logged without replacing the original error.
Outside these worker scopes, writes remain immediate.

## Progress and duplicate candidates

Routine deep-search updates are limited to five per second per lane. The first update, phase changes,
improvements, new failure states and final updates bypass throttling. Saving an improvement is never
conditional on whether a UI update was sent. Job cancellation/error handling remains authoritative.

Each deep search can remember 128 recent scored misses, with at most 256 retained feedback rows per
entry. Eligible duplicate ROM candidates skip repeated scope construction and the persistent miss
lookup. Keys cover the input revision, complete project/function/source snapshots, compiler identity,
repair rules and environment. Baselines, winners, failed compiles, custom compilers, external includes
and imported builds do not take this early shortcut. Ordinary persistent-cache validation remains.

## Measurements

Synthetic metadata pipeline, 300 candidates (function extraction, line lookup and layout diagnostics):
about 563 candidates/sec with separate readers versus 2,093/sec with candidate-scoped readers.
Repeated ROM fingerprint calls (100 trials, three calls per trial, 512 synthetic assembly lines):
122 ms versus 57 ms. Outputs were identical.

A structural transaction regression groups ten policy records (20 SQL statements) into one commit,
while confirming that a subsequent exact match is visible immediately. These are isolated stage
measurements, not promised end-to-end search speedups; compiler execution can still dominate.
