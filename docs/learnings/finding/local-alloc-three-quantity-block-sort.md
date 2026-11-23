---
title: local-alloc three quantity block sort
kind: finding
compiler: GCC 2.7.2 (KMC), local-alloc.c
evidence: observation
date: 2026-11-23
---

The rule, as the compiler's source states it:

`local-alloc.c` `block_alloc` orders a block's local quantities by priority (`floor_log2 (refs) * refs * size / life`)
with `qsort`, except when the block has exactly three or two: it sorts them itself,

    case 3: if (qty_compare (0, 1) > 0) EXCHANGE (0, 1);
            if (qty_compare (1, 2) > 0) EXCHANGE (2, 1);
            /* fall through */
    case 2: if (qty_compare (0, 1) > 0) EXCHANGE (0, 1);

and `qty_compare (q1, q2)` takes quantity *numbers* (born order in the first scheduler's output), not positions in
`qty_order`. After the first `EXCHANGE` the second and third comparisons look at the wrong pair, so with three quantities
the allocation order is not the priority order: with priorities P(M) < P(c) < P(W) and born order M, W, c the order comes out
M, W, c (lowest priority first); W, c, M needs other births (W first), or a fourth quantity, or one quantity made global
(then two remain and the two-quantity sort is right).

The evidence:

A 603-instruction state machine's arm `lui; lw; li; sh; and ...` (a word read, a mask constant, a constant half-word store)
allocated the mask `$v0`, the word `$v1`, the constant `$a0`; the target had `$v0`, `$v1`, `$a0` for word, constant, mask.
Making the mask a global pseudo (written into the local the sibling arms keep their result in) left two local quantities,
properly sorted, and 180 -> 120. Reproduced with lldb on cc1: `find_free_reg` was called in quantity-number order for the
block.

What it does not say: it does not say which spelling a source needs; `rmw_result_local` offers the one that worked. One
function, observation level.
