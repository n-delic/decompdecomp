---
title: A 394-instruction menu-update function, saved best 590, matched by hand (allocation priorities, operand order, call-first compare)
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 394-instruction N64 menu-update function (a slot
index stepped and clamped, a table of 0x18-byte records walked by a 14-iteration counted loop, a switch of flag
setters), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, project header with `u8/u16/s32` typedefs. The saved best (590, an
earlier permuter's) was m2c's text with a function-wide `do { } while (0)`, inline add helpers (`inline_fn23(a, b)`
returning `a + b`), an identical-arm guard and copy locals. Every step below was measured with the app's own judge
(`fast_judge`), then the final source submitted: score 0, byte-identical.

The source change, and the measured result before and after (scores, the judge used), in the order they were found:

1. Identical-arm guard removed AND the stepped copy advanced in place inside the arm that uses it (`temp = cur + 1;`
   became `cur = cur + 1;` at the head of the arm, uses renamed): 870 -> 590. The guard alone 870 -> 880, the in-place
   step alone 870 -> 1071, in place at the old position (guard removed) 710. Library: `collapsed_guard_inplace_step`.
2. The `& 0xFFFF` mask helper returned `unsigned char`: two `andi 0xff` rows against the target's `0xffff`. Widening the
   return type to `unsigned short`: 590 -> 415. Library: `mask_helper_return_width`.
3. Two locals of the saved best merged by the app's own pass (`disjoint_local_merge`): 415 -> 215.
4. `if ((u8) table[i] < (u32) next())` written call-first, `if ((u32) next() > (u8) table[i])`: the load no longer has
   to live across the call in a callee-saved register: 215 -> 85. Library: `call_first_compare` (`comparison_sides`
   handles only two plain loads).
5. Each inline add helper called with its two arguments the other way round fixed one `addu` operand-order row (5
   points each, 85 -> 35 over five calls, with the app's `later_operation_reuse` merging two phase locals). Library:
   `add_helper_operand_swap`.
6. The register rows that remained (a pointer local and a table address taking each other's `$s` register) came
   from the global allocator's priority order (`floor_log2(refs) * refs / live_length`, `global.c`
   `allocno_compare`): reusing the dead pointer local for the loop's table (`p = &table;` before the loop, `&table` in the
   loop written `p`) made it worse alone (65 -> 110), and only with the function-wide `do { } while (0)` narrowed to
   start at the loop (references before it are weighted one loop level less) did every register fall as in the
   target (110 -> 20). The reuse takes the loop's table pseudo and the pointer into one web of 19 weighted
   references, below the stepped index's priority, so the index keeps `$s2` and the web gets `$s3`. Library:
   `dead_pointer_address_reuse` (the combination as one child, `dead_pointer_reuse_fence_shift`).
7. Last two rows: `off + ((u16) f()) * 8 + &table` was re-associated by `fold` into `(off + &table) + f() * 8`
   (`split_tree`, `fold-const.c`: the constant address is hoisted out of a sum) where the target adds the table address
   to the scaled call result first. Holding the call result in a `u16` local (widened only at its use), staging the other
   addend in a local after it and joining them through the inline add helper: 10 -> 0. The hoist alone and the staged
   plain `+` scored 10 and 5. Library: `call_result_halfword_local` (`call_result_sum_helper`).

Failed experiments that would otherwise be repeated: moving the `do { } while (0)` start while the pointer still had
its own local did nothing (the fence weight only matters once the web exists); a separate local for the first use of
the stepped index put it in `$a0` (it must be one variable across the function); writing the call result's sum as one
plain expression or with extra identity helpers (`inline_fn2(&table)`) changed which instructions the scheduler saw
first (scores 105-605); unifying the loop counter with the call-result local (245) moved the widening instructions.

What is controlled evidence and what is one function: the allocator mechanism (a pseudo's priority decides which of two
overlapping webs takes the lower register, and `do { } while (0)` loop notes weight references) is read in
`global.c`/`flow.c` and was reproduced by the priority table `passes.py` prints (19 refs / 57 live = 1.33 against the stepped
index's 1.02, then fixed by the fence's weighting); the operand-order and association rules are measured on this function
only. The match relies on scaffolding the app's polish pass may tidy: the function-wide `do { } while (0)`, a `u16`
call-result local and a staged addend local, inline add helpers.
