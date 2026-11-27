---
title: && loop exit, test assignment and an arm-head loop note at -O1
kind: function
compiler: gcc-2.7.2-kmc
evidence: controlled
date: 2026-11-27
---

A 280-instruction N64 table-copy routine, `-G0 -mips3 -mgp32 -mfp32 -O1`. A plain rewrite with typed records scored 560
at once (the autopilot note's `-O1` was right; the old default-profile best was 3835 and a poor start).

Four edits, standalone judge, each from the 560 source:
- `do {..} while (a < K && i < 16);` folds to one `and`; `if (!(a < K)) break;` before `while (i < 16)` gives two
  branches (alone 345).
- `id = T[0].id; while (id != 0xFF) {..; id = T[i].id;}` as `while ((id = T[i].id) != 0xFF)` (alone 330).
- `do { } while (0);` first in `if (v >= i) { times[i] = a; i++; }`: the target zero-extends the `u16 i` again for the
  subscript, and cse otherwise reuses the test's (alone 455). A loop-end note stops cse's extended block.
- the first three together 10; then `i <= v` for `v >= i` gives 0.

Failed: eight other spellings of the compare and subscript (`*(i + a)`, `i & 0xFFFF`, `while`/`break`,
`do {} while (0)` around the store alone) all scored 105.

The family is decomp-repair `loop_exit_cse_forms`. One function; that cse stops at the loop-end note is a reading of
the behaviour, not confirmed in the compiler source (hypothesis).
