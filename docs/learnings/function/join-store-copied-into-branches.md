---
title: A store after an if/else tree, written in every branch, keeps cse2 from reusing its address
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-27
---

A 288-instruction N64 game initialiser. Symptom: the target builds a global's address with a `lui` at each of two
stores; ours built it once, kept it in `$a0`/`$v1` and stored through it later. The shape was

    if (fresh) { if (easy() == 0) { ..; } else { ..; } } else { .. }
    level = 0;
    if (a != 0 || b != 0) { level = pick; }

The `cse2` dump showed the second store's `high` replaced by the first's register: the pass skips around the `||`'s
second test. With `level = 0;` written at the end of every branch of the tree (decomp-repair `join_store_into_leaves`),
the join label starts a block of its own and the final jump pass merges the copies again.

Measured with the app's judge (first parameter already `u8`, the first switch already literal stores per `else if`):

- one store after the tree: 1085;
- bare copies in the three branches: 1235;
- copies in `do { } while (0)`: 0.

That the empty loop is needed because the scheduler otherwise lifts a branch's copy above its loads, which leaves the
tails unequal, is a hypothesis from the emitted assembly (one branch stored before its load).

The other steps, one function each: the first parameter `s32` to `u8` (1595 -> 1060; the entry's parameter copies came
in the target's order), and m2c's `var = K; goto block; ... block: G = var;` as an `else if` chain of stores (neutral
alone, 1060 -> 1085, needed for the match).

Library steps, measured: from the saved best with the parameter narrowed (1060), `join_store_into_leaves` gives 115 and
then a `conditional_store_arms_repairs` child gives 0. On the saved best itself (1595) `join_store_into_leaves` alone
gives 650; the chain from there was not judged.

Failed, so not to repeat: `volatile` on the stored global and the tested flags, a cast on the second store, and
`do { } while (0)` between the two stores (reached 115: the address stayed shared); inline helpers for the condition or
for the whole reset.
