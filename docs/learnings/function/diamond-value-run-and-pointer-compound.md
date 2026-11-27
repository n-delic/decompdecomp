---
title: a diamond's value run written per arm, and a pointer update as a compound store
kind: function
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 114-instruction N64 sound starter: two copies of one voice setup (a shared record behind a `volatile` pointer to a
`volatile` record), each choosing a level with `if (kind == 2)` and writing it three times.

The source change, and the measured result before and after (the project's judge). From the saved m2c draft (645):

1. m2c's form, `if (c) v = A; else v = B; p->level = v; p->level = v; p->level = v;`, written as the three stores with
   the literal in each arm, in both diamonds: 645 -> 110. The target merges the arms' identical tails itself, so only the
   first pointer load and the constant differ between the arms. decomp-repair `branch_value_run_sink`.
2. `*(p + 0x18) = (s32) (*(p + 0x18) + 0x10);` through the `volatile` pointer written as `+= 0x10` (the long form loads
   the pointer twice): 110 -> 0. decomp-repair `deref_compound_store` (535 on the draft without step 1).

The app's record was matched from a different source: a `static inline` helper adapted from a matched neighbour of the
same shape (three calls there, two here, other constants), exact on the first compile. Reading the matched neighbours
the brief lists before touching the draft is the cheapest move when one shares most of the code.

Failed experiments that would otherwise be repeated: only the first diamond rewritten scores 1817 (worse than the 645
seed), so the two go together; keeping the variable (`v = A; p->level = v; ...` in each arm) scores 210 with the registers
one round off.

What is controlled evidence and what is one function: the three judged steps are one function; the families have unit
tests in decomp-repair. No transfer-a-neighbour's-helper family exists: a declared repair is given the callees' C only,
not a look-alike's.
