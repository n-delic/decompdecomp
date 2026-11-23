---
title: a pointer built from a sum shares a later sum through cse; a global base read after the index statement loads late
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 218-instruction N64 object-matrix function (KMC GCC
2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`), 0xD4-byte records indexed by a counter. Saved best 220 with only register and
order rows: the record base `records = G` in `$a1` where the target has `$a2`, the 0xFF compare constant in `$a2` where
the target has `$a1`, one add order, and a global's `lui/lw` sitting four insns into an index chain where the target
has it before the chain.

The source change, and the measured result before and after (scores, the judge: the app's own `judge-worker` through
`fast_judge.py`, then `compile_and_diff`): edit list.
1. The call argument `(u32)records + (u32)(temp_s1 + 0x9C)` written `(u32)temp_s1 + (u32)records + 0x9C`: alone 480
   (the sum is now shared with `parentRecord = (R *)(records + temp_s1)` by cse, `addiu $a1,$v0,0x9c`, and `records`
   dies early, which shifts every saved register).
2. `parentRecord = 0;` before the call statement (a dead store flow deletes): alone 220 (the sum was not shared), with
   step 1 120: cse finds no register holding `records + temp_s1`, the add stays beside the call (`addu $a1,$s1,$a2;
   addiu $a1,$a1,0x9c`) and the registers match.
3. `recs = G;` written before the index statement `off = arg0 * 0xD4;` and used for the two reads before the next call
   (`recs + off`), the third read after the call kept as `G`: 120 -> 0. `off` before `recs`, the statements permuted,
   the index inlined into the sum (5, the operand order swapped) and `(off=...)` inside the sum (120) all missed it.

Compiler reading (cse.c, global.c; a hypothesis the measurements agree with): `records` takes `$a1` because
`(plus records 0x9c)` is the first operand of an insn whose result local-alloc already put in `$a1`, and global.c's
`set_preference` gives the first operand that register as a preference; with the sum written `s1 + records` the
preference goes to `s1` (it crosses a call and cannot use it) and `records` falls to `$a2`, leaving `$a1` to the
constant. The load order is sched.c's tie on the original order of the insns.

Failed experiments that would otherwise be repeated: eight spellings of the argument alone (all 480-600, the shared
sum shifts the saved registers), a `do { } while (0)` around the call or the loads (600+), a named label before the arm
(cse does not see it: the label was deleted), `-fno-cse-follow-jumps` / `-fno-cse-skip-blocks` (no change), a base
local assigned after the index statement (120).

What is controlled evidence and what is one function: one function, matched (recorded 0 in the app). The two
decomp-repair families (`pointer_sum_cse_barrier`, `global_base_before_index`) reproduce 220 -> 120 -> 0 from the saved
best in the isolated judge; their tests use synthetic sources. The match relies on the dead store of step 2
(scaffolding: a faithful source probably reused that pointer for another record) and on the base local of step 3.
