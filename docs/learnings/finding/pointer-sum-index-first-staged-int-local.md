---
title: A pointer sum the target adds index first wants an integer sum in a local of its own
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

Two twin functions of an N64 KMC game (42 instructions each, the minimum of a record field over a table), both at 5
with one row: `addu $a0, $v0, $a0` in the target, `addu $a0, $a0, $v0` in ours, for
`temp_a0_2 = (arg1 * 0x18) + p;` with `p` a `void *` (inside a `do { } while (0)` fence that is load-bearing: 830
without it).

The source change, and the measured result before and after (scores, the judge used):

`{ s32 sum; sum = (arg1 * 0x18) + (s32) p; temp_a0_2 = (void *) sum; }`: 5 -> 0 on both (app judge). The C front
end builds pointer arithmetic pointer first, so no operand order of the pointer sum changes the `addu`; an integer
sum keeps the order written. Assigned straight to the pointer (`temp_a0_2 = (void *) ((arg1 * 0x18) + (s32) p);`)
the order was right but the score 45: the loop dump shows expand computing the index's sign extension into
`temp_a0_2` itself (`(set (reg/v 81) (ashiftrt ...))`), which made it a longer-lived global pseudo that lost `$a0`.
A local of its own (or the product in a local, `off = arg1 * 0x18;`, also 0) keeps the sum's destination free.
decomp-repair `byte_pointer_sum_staged` (0.6.477) offers both forms; `prove_chain` takes both saved bests to 0.

Failed experiments that would otherwise be repeated:

Swapping the operands of the pointer sum, a `char *` add, declaration order (5, from an earlier agent); the integer
sum assigned to the pointer with every cast of the product (`(s32)`, `(u32)`, `(s16)`, `* 3 * 8`): 45 each; writing
the sum back into the fenced copy or into `temp_a0` itself: 5 (expand_binop puts an operand equal to the target
first).

What is controlled evidence and what is one function:

One pair of twins. The front end's pointer-first sum and the target propagation are read from the dumps; that the
staged local fixes allocation in general is a hypothesis.
