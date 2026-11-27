---
title: a state byte stored in every leaf of the if/else, not from a result variable at the join
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 70-instruction N64 transition step. When a counter ends, one arm tests five globals and sets a state byte to one of
two constants, the other arm calls an empty stub and sets a third constant. The target builds each constant in `$v0`
(in the branch delay slots) and has one `sb $v0` after the join. The machine draft held the constant in a result
variable (m2c's nest of repeated defaults) and stored it once at the join: it got `$a0`, and the store was copied into
`bnel`/`beql` delay slots. The stub's `jal` has `addiu $a0, $zero, 4` in its delay slot, which the draft's call did not
pass.

The source change, and the measured result before and after (scores, the judge used):

The app's judge. Three existing decomp-repair families (0.6.388) reach the match from the saved draft, each step
better than the last (`prove_chain.py --families default_nest_ladder,join_tail_arm_inline,target_call_arity`):

1. `default_nest_ladder`, the nest of defaults as an `else if` ladder: 2115 -> 1320.
2. `join_tail_arm_inline`, the join's store written in every leaf: 1320 -> 200.
3. `target_call_arity`, the stub called with the constant the target sets: 200 -> 0.

By hand, `if (a && b && ...) { p->state = 4; } else { p->state = 3; }` with `stub(4); p->state = 6;` in the other arm
is also 0, as are nested `if`s with a store at every leaf. No scaffolding.

Failed experiments that would otherwise be repeated:

All from the draft with the argument added (1915) unless said otherwise, none moved it: a `s32` result variable;
`p->state = (a && b && ...) ? 4 : 3;`; the result variable kept and the store copied into the two outer arms only (with
the m2c nest, with `x = 3; if (...) x = 4;`, or with an `if`/`else` setting the variable). `p->state = 3; if (...)
p->state = 4;` scored 1010. The `else if` ladder with the argument and the store left at the join scored 980.

What is controlled evidence and what is one function:

One function. The scores are measured; why the result variable gets `$a0` here was not read in the compiler (hypothesis:
the argument copy of the other arm gives its pseudo a preference). The app's earlier Deep Permuter pass on this
function ran before `default_nest_ladder` was released.
