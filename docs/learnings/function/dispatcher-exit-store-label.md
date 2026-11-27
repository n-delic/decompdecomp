---
title: a state dispatcher whose shared store sits in an early arm (empty last case, store written per arm)
kind: function
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3
evidence: verified
date: 2026-11-27
---

What the function looked like: a 158-instruction state machine, a jump table over a global state word; most arms call
a function, store the next state and call another; some arms only set the next state and jump to one shared store before
the epilogue. m2c wrote that store as `block_35: D = var; default: return;` inside the first arm that reaches it, with
`var = K; goto block_35;` elsewhere, and left out the table's last entry, which goes to the default.

The source change and measurements (app judge): machine draft 1630. Copying `D = var; return;` over each goto: 150.
An empty `case` for the last table entry (`switch_range`, the bound check was one short): 0. The compiler's
cross-jumping then merges the stores in the last arm, where the target has them. `D = K; break;` per arm also gives 0.

Failed experiments: `switch_range` alone stops at 1480 (the join stays in the early arm).

Controlled evidence: one function; decomp-repair `goto_arm_tail_inline` and `switch_range` reach 0 from the draft in
two steps.
