---
title: v &= -(cmp) takes the target's and-operand order when the compare is held in a block local
kind: function
compiler: KMC GCC 2.7.2 -O1 -g
evidence: verified
date: 2026-11-27
---

What the function looked like and the compiler: a 144-instruction N64 collision loop (KMC GCC 2.7.2 `-O1 -g`, `-G0 -mips3 -mgp32 -mfp32`), at 5 rows from exact. One statement, `var_s1 &= -(*(u8 *)((s8 *)arg0 + 0x50) != 0);` in the arm of an `if ((var_s1 & 0xFF) == 0) ... else`, differed only in the operand order of the `and` (`and $s1,$s1,$v0` in the target).

The source change and result: `{ s32 flag = *(u8 *)((s8 *)arg0 + 0x50) != 0; var_s1 &= -flag; }`. Judge (`fast_judge`): 5 -> 0. The new decomp-repair module `flag_local_negated_mask` offers it for every `V &= -(single comparison);` (one child per site, and one with all), the local named `flag` unless taken.

Failed experiments that would otherwise be repeated: none recorded for this function; the polished form was found by hand.

Evidence: one function, verified by the judge. Hypothesis: the commutative `and`'s operands come out in register-number order, and a named pseudo for the flag has a lower number than the compare's expression temporary; not traced in the compiler source.
