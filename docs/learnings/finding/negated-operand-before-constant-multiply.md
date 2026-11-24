---
title: negated operand before constant multiply
kind: finding
compiler: SN/KMC GCC 2.7.2 -G0 -mips3 -O2 (N64)
evidence: observation
date: 2026-11-24
---

What it looked like: a 21-instruction N64 setter (SN/KMC GCC 2.7.2, `-G0 -mips3 -O2`) that adds a scaled global to a
float field: `field + (f32) (g * -0x14)`. The target loads `g`, runs `negu` at once, then the shift/add chain for x20.
The compiler's own order for `g * -0x14` is the chain first and `negu` on its result (7 rows, 5 register, 2 order).

The source change, and the measured result: moving the negation onto the operand, `(f32) (-g * 0x14)`, scored 85 -> 0
(fast_judge, the app's judge). `t = -g; ... t * 0x14` and a temporary for `t * 20` also score 0. `e * -K`, `(-e) * K`
and `-(e * K)` are one value and three trees; the tree decides where the negation sits relative to the multiply
expander's shift-and-add chain. Hypothesis, not confirmed in the compiler source: `expand_mult` is given the operand
as written, and a negative constant takes a negate-the-result path.

Failed experiments: none of the 163 children of the other families (`prove_chain --families all --combine 10`,
including `product_split_sum`, `temporary_hoist`, `local_weight_ladder`) improved on 85. The family is decomp-repair
`product_negation_move` (0.5.1020): integer operands only, no calls, side effects, volatile or float names.

Evidence: one function (not a controlled regression test). Over the project's 1128 matched sources the family fires
once (on that function), so it adds almost no noise to the search.
