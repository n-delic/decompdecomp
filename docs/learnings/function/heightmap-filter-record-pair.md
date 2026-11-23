---
title: heightmap filter record pair
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

A 242-instruction heightmap smoothing filter (two loops: random seeding through a pair of scratch globals, then a
4-neighbour average with clamped row and column indices), KMC GCC 2.7.2, saved best 2210, exact at 0. The globals are
the loop counters, two index globals and a neighbouring pair of scratch words (`D_...7C`, `D_...80`).

The edits, each with the exact judge's score on the function (one run; the families that now offer them are in brackets):

- clean rewrite on the m2c shape, pair written `p[0]`/`p[1]`: 4595; pair pointers set inside the loop body, products
  as locals: 2145; the float-to-int result sharing the index variable: 1695.
- `v = t - 1 + g;` split into `q = t - 1; v = q + g;` per arm (fold-const reads the sum as `t + (g - 1)`, which loads
  `g` first and takes the register the sum needs): 945 -> 240 [`term_split_local`].
- the `else if (h - 1 < t) {A} else {B}` chain written `else if (t <= h - 1) {B} else {A}` (branch polarity, the
  out-of-line arm): 260 -> 0 on the finished source [`else_if_arm_swap`].
- the loop counter global read once into a local above the first of the stores through the pair pointer (the compiler
  reloads a global after every store through a pointer): 805 -> 295 [`global_read_local`].
- the first loop's selection temporary and the second loop's clamped index written as one variable: 240 -> 145
  [`disjoint_scalar_merge`].
- the copy `y = x` above `if (x < 0) {...} else if (w < y)` moved into the else arm, with the later reuse of `x` (the
  float-to-int result) given its own local: 835 and 690 alone, 240 -> 135 together [`else_arm_copy_split`]. cse follows
  the jump to the else arm and makes `y` and `x` one quantity, ranking the longer-lived register first; with `x`
  reused further down it stays first and the compare reads `x`.
- the pair `pa = &G; pb = pa + 1; *pa ... *pb` written as a record pair (`pa->a`, `pb = &pa->b`): 135 -> 0
  [`pair_pointer_struct`]. After register allocation the second scheduler (post-reload sched.c, a hard-register address)
  keeps the reload of the counter below a store through an unknown register address, but a MEM_IN_STRUCT store at a
  varying address never conflicts with a plain global (`true_dependence`), so the load moves above the four stores to the
  pair, where the target has it. The per-edit rows and the pass dumps (first scheduler order unchanged, second scheduler
  order differing) are what showed it.

Failed experiments that would otherwise be repeated: reordering the statements of the block (all permutations of the
four neighbouring statements scored alike), declaration order (no effect in cc1), `do { } while (0)` fences (worse), a
second assignment of the pointer to make it "unknown" to the first scheduler (cse and loop.c keep a REG_EQUAL note, so
the first scheduler still knew it), `const u8` reads (the loads then float freely, a different shape), merging the
reloaded counter into other locals (the order stayed, the registers rotated), and the flag `-fno-cse-follow-jumps`
(moves the compare to the right register and breaks others: 155).

Controlled versus single: each family is shown on this one function from the source before its edit, with the exact
judge; they are not yet tried on other functions. The `else_arm_copy_split` and `pair_pointer_struct` explanations
are read from cse.c and sched.c (`make_regs_eqv`, `cse_end_of_basic_block`, `true_dependence`).
