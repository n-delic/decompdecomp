---
title: masked constant live source
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-26
---

A small N64 string-append function that m2c wrote in goto form (`loop_1`, `block_3`, `block_7`, `block_8`).

Source change and score (the app's standalone judge): the draft had `var_v0 = 0 & 0xFF;` on the scan's exit path, which
the compiler folds to `move $v0, $zero`; the target has a real `andi` there. Writing `var_v0 = var_v1 & 0xFF;` (the live
variable that is still zero on that path, masked as on the other path of the join) took 200 to 0. Nothing else
differed once the labels were the target's.

Now decomp-repair `masked_constant_live_source`: `X = K & 0xFF/0xFFFF;` where a scalar assigned the same literal earlier
in the function exists becomes `X = L & mask;`, gated on an `andi` with that mask in the target. `prove_chain.py
--families all` reaches 0 in one step.

Failed experiments: none recorded. The same function in an `-O1 -g` draft state (510) improved only to 310 by the same
edit, so the other rows there were a different problem.

Controlled: one function; the edit is exactly the family's.
