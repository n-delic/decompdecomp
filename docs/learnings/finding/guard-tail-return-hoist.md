---
title: guard tail return hoist
kind: finding
compiler: sn-n64-281 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

A small N64 function (SN-N64 2.81, `-G0 -mips3 -mgp32 -mfp32 -O2`) asks a helper for a pointer and, only when it is
NULL, builds one (two calls, two field stores in one arm) and returns it. m2c's draft kept a second local for the built
pointer and tested the first: `v = get(); if (v == NULL) { ...; s = make(); ...; v = s; } return v;`. The target has
one `move $s0,$v0` after the helper's call and branches straight to the epilogue on the non-NULL path.

Measured with the app's isolated judge:

| source | score |
|---|---|
| m2c draft (separate `v`, `s`, `v = s;`) | 105 |
| one local for both (`s = get(); if (s == NULL) { ... } return s;`) | 100 (the join copy `move $v0,$s0` lands in the branch delay slot) |
| the same inside `do { if (s == NULL) { ... } } while (0); return s;` | 10 (scaffolding) |
| `s = get(); if (s != NULL) { return s; } ...; return s;` | 0, no fence |

The rule: with the work after an early `return` rather than inside the `if`, the non-NULL path owns its own jump to
the epilogue and the NULL path's code follows in the fall-through, so the join copy never exists. Hypothesis from this
one match, not read from the compiler source.

Library: decomp-repair `guard_tail_return_hoist` (v0.5.1026, pinned in #3325) has three families:
`guard_tail_return_hoist` (`if (C) { BODY } return V;` -> `if (!C) { return V; } BODY return V;`),
`guard_join_local_unify` (merge the draft's second local into the call result) and `guard_join_unify_hoist` (both in
one child). `prove_chain --families all` from the draft reaches 0 in one step (the pair child); chained, unify 105 ->
100, hoist 100 -> 0.

Failed experiments: before the family, `prove_chain --families all --children 400 --combine 10 --lookahead 3` found
nothing below 105 from the draft or below 100 from the unified source (280 and 260 children); the existing
`early_return_guard_block` and `terminal_guard_layout` only go the other way (an early return into a block, or a
two-armed if with returns). The `do { } while (0)` fence reached only 10.

Controlled evidence: none beyond this function (verified on one function). Firing over one project's 3606 saved seeds
and bests (no judging): hoist 162 of the bests, unify 2.
