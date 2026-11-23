---
title: call result join copy and tick order
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function looked like: a 118-instruction menu update, KMC GCC 2.7.2. The target keeps two pseudos for one call
result (`move $a0,$v0` stored to a global, then after an `if` block `move $a2,$a0` in the delay slot of a `beqz`, with a
`j` after the then-arm), where the m2c draft has one. Saved best 470 (5 rows: the copy, the branch polarity and the
registers of the two pseudos).

Source changes and measured results (isolated judge, fast_judge):
- a local copy `item2 = item;` placed after the first `if` block (an adjacent copy is folded into the call's set by
  combine), later field stores and the trailing argument through the copy: 80 (the original is live to the end through
  the third argument of the call, so the registers are `$a2`/`$a3`);
- the same with m2c's stray third argument of the two-argument callee dropped (the callee's other callers pass two):
  60. Library: decomp-repair `call_result_join_copy` (#1327), 470 -> 280 from the scaffolded best;
- `else if (identity_inline()) {Y} else {Y}` written as `else {Y}` and the polarity swapped (`if (g & 1) {+1} else {+9}`):
  280 -> 60 (neutral alone). Library: `chained_identical_arm_flip` (#1329);
- the last two rows: the target loads the counter (`lhu D_800BFC94`) after the packed-colour `or` and before the
  volatile store; the source order store-then-increment gives the store first (the cast store is a non-struct memory
  reference, so sched.c keeps the later load behind it). Reading the counter into a local before the store and writing it
  after, with that local sharing the masked-colour variable (a write-after-read edge from the `or`), puts the load in
  place (60 -> 30), but the shared variable has two deaths and is a global pseudo, so the registers are `$a0` for it.
  `scalar_storage_reuse` then reaches 25. Not matched.

Failed experiments: a counter local of its own scores 460 (the load floats up to the shifted halfword: sched.c priority
is the longest path from the block start, a single load has priority 1); structs for the pulse words, volatile on the
counter, `-fno-...` flags, a non-volatile struct store (the store is then filled into the branch's delay slot instead),
sharing the shifted-halfword variable (the shift is scheduled behind the mask), statement permutations.

Controlled evidence: the first three rows are one function. sched.c reading: priority = max over predecessors of
(priority + cost - 1); the backward scheduler picks the highest first and a load costs 3, so a load with no predecessor
goes to the top of its block unless an edge (here a register anti-dependence) holds it.
