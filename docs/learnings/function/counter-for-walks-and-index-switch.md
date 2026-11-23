---
title: counter for walks and index switch
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like: a 301-instruction KMC GCC 2.7.2 menu builder (three loops over a record's pointer array,
the middle one a six-way `switch` on the loop index, each loop allocating a sprite object when its slot is empty).
Its saved best (1825) was a hand-kept m2c shape: do-while loops with pointer cursors stepped beside the counter
(`var_s1 += 4`, `var_s3 += 2`), a counter stepped by 4 and switched on (`case 0: case 4: ... case 20: case 23:`), a
cursor compared with `(2 * 4) + arg0`, every slot read through `volatile`, a `char` local, a scratch local that reused
the first loop's counter name, and each loop with its own counter. Earlier agents spent ~100 attempts (the notes
blamed the jump-table relocation and a branch-likely) and none reached the cause.

The source change, and the measured result (the app's judge, one function, so one case rather than a rule):

- Loops as `for (i = 0; i < N; i++)` over one index, slots as `arg0 + i * 4`, tables as `tbl + i`, `switch (i)` with
  `case 0: case 1:`, `if (i == 2)` for the compare with the element's address, plain (non-volatile) slot reads, one
  unsigned counter shared by the three loops: 1825 -> 805. loop.c turns `i * 4` into the cursor and the test into
  `sltiu $s1,0x18` and drops the `sll` of the switch's table lookup (a switch on the byte offset has none to drop), so
  the table lookup, the compare of the cursor with `arg0 + 8` (cse2 then reads slot 2 as `0x3ec($s5)` inside the arm)
  and the registers all come out as the target's. decomp-repair `walk_counter_for`.
- `char var_s6` as `s32` (m2c typed it from one `sb`): 805 -> 60 (`narrow_widening`).
- `var_v1 = 0x37;` written in the `else` of the later `if (((i == 0) | (i == 2)) == 0)`, though a bare block ends
  with it (the default sits one `}` before the `if`): 60 -> 0 (`default_into_else` now sees across a bare block).

Failed experiments that would otherwise be repeated: stripping `volatile` from the old shape alone (1825 -> 7535: the
cursor form then folds the reloads cse keeps in the index form); the index form with each loop's own counter (the
counter of the first loop then loses `$s2` to the cursors: one `u32` shared by the loops raises its priority); signed
counter with the `(u32)` test (`slti` instead of `sltiu` in the switch's range check: declare it `u32`).

What is controlled evidence and what is one function: one function, each step measured with the exact judge from the
saved best (`prove_chain` from the saved best with `walk_counter_for`, `narrow_widening`, `walk_counter_merge` and `default_into_else` reaches 0 in four steps: 1825 -> 760 -> 340 -> 60 -> 0; the merged-counter child of the first step scores 805 and a greedy chain only reaches it through `walk_counter_merge`). The jump-table `?local` operand rows that
the notes called unresolved were a symptom of the byte-offset switch, not of a relocation.
