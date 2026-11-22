---
title: field cursor index graph search
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-22
---

A 222-instruction best-first graph search (a node array, each node with a 16-entry neighbour pointer array, a float
cost, a parent link, an open list and calls into a heap helper). The saved best (190) walked each node's neighbour array
with a `char *` cursor: `q = (char *) cur; do { nb = ((volatile Node *) q)->nbr[0]; i++; ...
((volatile Node *) q)->nbr[0]->cost = ...; q += sizeof(Node *); } while (i < 16);`, and carried an identical-arm
`if (x) { S } else { S }` from a search. All of the remaining rows were one cause: the loaded neighbour sat in `$v1`
instead of the target's `$v0`, so `reorg` filled the float compare's delay slot from the join (`sltiu $v0,$s2,0x10`,
legal only because `$v0` was not live there) where the target has a `nop`.

Source change (exact judge, `fast_judge`, then `compile_and_diff`: matched): the natural loop
`for (i = 0; i < 16; i++) { Node *nb = cur->nbr[i]; ... cur->nbr[i]->cost = ...; }` with the identical arms collapsed.
Measured from the saved best: indexed loop alone 190 -> 20; identical arms collapsed alone 190 -> 300 (neutral or worse
by itself, the scaffolding problem); both 0. The volatile casts were scaffolding added to stop the reloads that the
float stores through `nbr[i]` already force. The compiler's strength reduction makes the stepped pointer from the
indexed loop (`addiu $s0,$s0,4` beside `sltiu $s2,$s2,16`).

The permuter had no family for a record-member cursor loop (`pointer_walk_index` only handles a global array), so
`prove_chain` over all families found nothing from the saved best. decomp-repair `field_cursor_index` (typed cast
form and m2c's `M2C_FIELD` form) offers the loop, and `field_cursor_index_strip` the loop with `scaffold_strip`
applied: one child that reaches 0 from the saved best.

Failed or neutral: removing the identical arms first (300, the volatile cursor kept). One function; the delay-slot
mechanism (the loaded value's register decides whether `reorg` may fill from the join) is read from the diff and the
register conflicts, not confirmed in `reorg.c`: a hypothesis.
