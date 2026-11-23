---
title: volatile step cursors are spilled givs
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 530-instruction function (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) whose innermost loop steps a counter by 1 and
two more variables by 3 and 6. The saved best (922) kept the two steps in `volatile s32` locals, padded the frame with a
`volatile s64 pad[2]` and wrapped the step statements in `if (c) { S } else { S }` (m2c's identical arms). The target
reloads them through `$t1` (`lw $t1,0xd4($sp); addiu $t1,$t1,3; sw $t1,0xd4($sp)`), holds the loop counter in `$s7`
and an argument in `$fp`.

The rule: those cursors are not objects of the original. loop.c strength-reduces `counter * 3` and `counter * 6`
into new pseudos (regnos above every user pseudo), and global.c gives them the lowest allocation priority, so reload
spills them. Written volatile they take the first frame slots (a frame 8 bytes short, `lw $v0` instead of `$t1`);
written as plain stepped locals they are user pseudos with a high priority and displace the argument.

The source change, and the measured result before and after (scores, the judge used):

Volatile dropped and pad removed alone: 4084 (the stepped plain locals take `$s5`/`$fp`). The step guard written once
too: 1277 (the duplicated arms double every reference global.c counts for the counter and the cursors, because
`reg_n_refs` adds the loop depth per mention). Cursors as `var_s7 * 3` and `var_s7 * 6`: 1263 with the target's
spilled cursors and 36 register rows left (the saved best had 922 with the cursors out of the allocator's reach).
decomprepair's `volatile_step_index` makes that whole change as one child. Exact judge (`fast_judge`) throughout.

Failed experiments that would otherwise be repeated:

Still open on that function: the target's counter is allocated before the argument (counter `$s7`, argument `$fp`).
The `-dl -dg` tables give the counter 19 weighted references over 156 insns (priority 0.487) against the argument's
40 over 358 (0.559) and the cursors' 19 over 149 (0.51), so the argument and a cursor win the two last callee-saved
registers. One more mention of the counter inside the loop (24 references, 0.604) restores the target's order, but
nothing the target's assembly shows is such a mention; a dead use (`j = counter + 1`) is deleted before the counts are
taken and changes nothing; copy locals, `++i` in the test, narrower counter types, moving the counter's start, and
removing the `do { } while (0)` wrappers (they change the loop depth the references are weighted by) did not reach it.
Duplicating the identical step arms raises the cursors above the argument as well (0.748), the wrong order.

What is controlled evidence and what is one function:

One function. The weights are read from cc1's dumps (`reg_n_refs += loop_depth` in flow.c, priority in global.c), the
effect of each source change from the exact judge; the order the target needs is inferred from its assembly.
