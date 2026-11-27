---
title: a sparse switch of twenty arms, osSetTime(0) and an -O1 prologue
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 282-instruction N64 game state machine: a sparse `switch` on a halfword (about twenty arms, a compare tree, no jump
table), then a few globals and `osGetTime`. Built with `-O1` (the target saves $ra, $s2, $s1 before it copies the
parameter); a matched neighbour that calls the same helpers was already `-O1`.

The source change, and the measured result before and after (scores, the judge used):
From the saved best (1450 under `-O1`), one library family per step, app's judge:
- arms in the order of the blocks the compare tree jumps to (`compare_tree_arm_order`): 300;
- `f(0, 0)` as one 64-bit zero argument, `osSetTime(0)` (`wide_zero_argument`): 10. The 64-bit move is two
  instructions, so reorg cannot put it in the delay slot of the `jal` and takes the last register save instead;
- `func(k + base)` with the integer first (`integer_first_call_address`): 5;
- `$v0`/`$v1` stored one word each as one `unsigned long long` store (`wide_return_store`): 0.

Failed experiments that would otherwise be repeated:
Under `-O2` and its `-fno-schedule-insns*` variants the best was 290-410: the prologue stayed in another order.

What is controlled evidence and what is one function:
The four steps and their scores are measured on this one function. That reorg skips a two-instruction move is an
explanation consistent with the output, not read in reorg.c (hypothesis).
