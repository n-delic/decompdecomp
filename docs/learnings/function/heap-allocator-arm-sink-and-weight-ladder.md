---
title: an N64 heap allocator's register rows: a complement sunk into its arm and a size's statements wrapped deep (115 -> 15)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags: a 110-instruction heap allocator (a two-mode
bump allocator on a tag table, then a free-list walk in a do loop), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, saved
best 115: every instruction and offset right, 23 register rows. The saved source carried `volatile` on the heap globals
and the block fields; removing every `volatile` changed nothing (115 before and after), so it was scaffolding.

The source change, and the measured result before and after (scores, the isolated judge, one worker):

1. The complement of the alignment mask (`s32 inverse = ~mask;`) was computed before `if (tag == 45) {..} else {..}`
   and read only by the else arm. Sunk into that arm as its first statement, `do { inverse = ~mask; } while (0);`, with
   the arm's declarations split from their initialisers so its loads come after it: 115 -> 85 (the mask took `$a2`, the
   table offset `$t0`, as in the target; the same move without the scope, 900). decomp-repair `arm_invariant_sink`.
2. The free-list walk's block size (`avail`) and the pointer carved from it (`newBlock`) had their registers exchanged:
   weighted references from the source (a reference is 1 plus the loop depth, flow.c; the `-dl -dg` table agreed) were 14
   over 31 live instructions for the size (priority 1.36) against 18 over 27 for the pointer (2.67). Splitting the loop
   block's initialised declarations and wrapping the three statements that name the size and not the pointer
   (`avail = block->size;`, `end = (u8 *)block + avail;`, `avail = size + 12;`) three, one and three deep, with the
   load `heapFree = D_800E1A9C;` pinned once behind them: 85 -> 15. Needs seven more references on the size (21 over 31 =
   2.71 against 2.67); the same three at depths 3, 2, 3 alone is 75, the pin alone and every other depth 85 or worse.
   decomp-repair `local_weight_ladder` (the shape is the 325th of the 500 children it offers from the 85 source).

Failed experiments that would otherwise be repeated: the weights of every statement of the first branch wrapped up to
four deep in pairs and triples (nothing under 15); the complement before the branch (a global pseudo; the register
triple of mask, table offset and complement stays `$v1`, `$a2`, `$t0` in forty statement orders and expression forms);
a single return variable shared by both arms (an extra `move` in each, 1190); the complement's variable reused as the
result, with or without a scope or a temporary (mask back to `$v1`, 305-1165); `-fno-schedule-insns2` and `-O3` (same
15), `-fno-schedule-insns` (85). What is left at 15 is three rows of the else arm: the target holds the complement in
`$v0` and the `(old + mask)` sum in `$v1`, where the source keeps the sum and the result in one block-allocator
quantity (local-alloc.c `block_alloc` ties the output of `and` to its first operand when that operand dies there, so the
sum takes `$v0`); with the complement first, the mask has no conflict with `$v1` and takes it instead of `$a2`.

A second route reaches the target's registers for the arm (but not its other two): the sum written into the arm's `end`
variable (`end = old + mask; start = end & inverse; end = start + size;`). That variable then dies twice, so local-alloc
leaves it to global-alloc, the tie to the result cannot happen, and the arm's complement, sum and result land in
`$v0`, `$v1`, `$v0` with the mask in `$a2` and the table offset in `$t0` as in the target (35 on the 15 source's arm
rewritten this way, with the weight ladder above; 7 register rows left). What it leaves is `old` and `end` exchanged: `old` is a block-local quantity and
takes `$v1` first, the global `end` takes `$a1` (it conflicts with `old`'s `$v1`), and the target has them the other
way. Hand-off note: a source where `end` is local (one death) while the result is not tied to the sum, or where `old`
is global with a lower priority than `end`, would close it; not found in a grid of 250 statement orders, fences and
expression forms. These are hypotheses read from local-alloc.c and global.c, not confirmed in the compiler source.

What is controlled evidence and what is one function: everything above is one function. The weights arithmetic and the
tie rule are read from the compiler's source and its dumps; the delay slot that holds the complement in the target is
filled by reorg only from the arm's first instruction or from above the branch.
