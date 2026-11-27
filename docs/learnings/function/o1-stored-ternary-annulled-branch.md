---
title: o1 stored ternary annulled branch
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 246-instruction N64 menu state routine
(`switch` on a u16 slot state, frame saving `$ra/$s2/$s1/$s0`). Every callee register is saved before the parameter
is copied, which names `-O1`; the saved best had been tuned under `-O2` with search scaffolding (2230) and a rewrite
from the assembly under `-O1` scored 200 with two small gaps.

The source change, and the measured result before and after (scores, the judge used), app judge:

- A pointer to a flag word read through `$s2` (`lw $v1,0($s2)`, later `lhu -0x68($s2)`): `s32 *p = &G; do {} while (0);`
  before the first read keeps the address in a register; without the empty loop cse folds it back (1141 -> 200).
- A volatile global clamped with `G = (G >= 0x22) ? 0x22 : G + 0xA;` compiled to an annulled `beql`; the target has
  `beqz` with the constant in its delay slot. `{ s32 v; if (G >= 0x22) v = 0x22; else v = G + 0xA; G = v; }` is exact
  (200 -> 0). Decomp-repair `ternary_result_branches`.

Failed experiments that would otherwise be repeated: the other arm order (`if (G < K) v = G + n; else v = K;`) stayed
200; `v = 0x22; if (...) v = ...;` and `v` set before the test scored 1430-2200; dropping `volatile` scored 435-960.

What is controlled evidence and what is one function: one function. The jump.c explanation of the annulled branch is a
hypothesis, not read from the compiler's source.
