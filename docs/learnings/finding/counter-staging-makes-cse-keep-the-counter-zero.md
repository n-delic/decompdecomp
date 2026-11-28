---
title: counter staging makes cse keep the counter zero
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: verified
families: post_loop_counter_stage, accumulate_separate_dest, disjoint_local_merge, block_local_share
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 checksum check (83 instructions, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g`): decode a buffer in a loop with
calls, then `sum = 0; for (i = 0; i < n; i++) sum += buf[i]; return (sum & 0xFF) != expect ? 0 : n;`. The target has
`move $v1,$zero` (sum) first and `move $s0,$zero` (counter) in the delay slot of the loop's entry test
(`andi $v0; move $a0,$v0; blez $v0; move $s0,$zero`); ours had the two zeros the other way round, plus `addu $v1,$v1,$v0`
where the target adds the byte first.

The source change, and the measured result before and after (scores, the judge used):

- 15 -> 10: `t = buf[i] + sum; sum = t;` (decomp-repair `accumulate_separate_dest`, already in the library).
- 10 -> 0 (15 -> 5 alone): the post-loop read staged in the counter, `i = sum & 0xFF; if (i != expect)`. decomp-repair
  `post_loop_counter_stage` (0.6.x, PR RetroReversing/decomp-repair#2121) offers it.

Why, read in gcc 2.7.2's `cse.c`: `insert_regs` makes the second register set to `0` equivalent to the first
(`make_regs_eqv`), and the canonical register of the quantity is the one whose last mention comes latest. The
accumulator is read after the loop, so it is canonical whichever zero is written first; cse rewrites the copied entry
test `i < n` into `sum < n`, combine folds `sum = 0` into that test (keeping its set at the test's place, next to the
branch), and reorg fills the delay slot with it. A mention of the counter after the accumulator's last read makes the
counter canonical and the target's order follows. The dump that shows it: the `.cse` dump (`passes.py --save`) has `(lt (reg sum) (reg n))`
in the copied test.

Failed experiments that would otherwise be repeated:

Every order and spelling of the two zeros (either order, `sum = i = 0`, both in the `for` header, a `while`, the bound in
a local, the sum through a temporary copied after the loop): 15 each. `if (n > 0) { i = 0; do .. }` shrank the frame by
8 (0x48 -> 0x40) and scored 422-632. `return ... ? 0 : i` (counter as the result) fixes the zeros but puts the result
in `$s0` (110). A dead `i = 0;` after the compare also gives 0 but is not the game's C.

Two more functions of the same game in this run, both closed by families the library already had:
- A table lookup and range search (75 instructions) profiled `gcc-2.8.1-pm-sn-as -O1` stuck at 160 on its epilogue
  (`addiu $sp; jr $ra; nop`, which 2.8.1 always fills): under `sn-n64-272-0001 -G0 -mips3 -O1` the saved C was 20 and
  `or_arm_split` then `merge_integer_lifetimes` (or `disjoint_local_merge` on m2c's `temp_fv1`/`temp_fv1_2`) gave 0. The
  profile was the gap; the trial applied with `suggest`.
- A display-list writer (70 instructions) at 70 with the two `D_800AD1E4` writes sharing a function-wide `temp_v1`:
  one block per write with its own pointer and stores gave 0 (`block_local_share` finds it).

What is controlled evidence and what is one function:

The cse rule is read in the compiler source and reproduced on this one function by the exact judge (each step's score
above); that it decides other loops of this shape is a hypothesis the family lets the judge test.
