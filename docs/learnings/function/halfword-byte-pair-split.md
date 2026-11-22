---
title: a halfword built from two bytes keeps its andi 0xffff only as two sets of a u16 local that is passed on
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 62-instruction sound-engine function (N64, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, saved best 105) differed
from its target by one missing instruction: `lbu v0,7; lbu v1,0; sll v0,8; or v1,v1,v0; andi a0,v1,0xffff;
beql a0,zero` (the target) against the same without the `andi`, the test reading `$v1`. The next call
(`jal func_8009846C`) takes that value as its argument; m2c had written the callee as `f()` with no argument.

Source change, 105 -> 0 (the app's judge, `compile_and_diff`, linked bytes identical): the local `u16`, the value built
as `id = *(u8 *)p; id |= *(u8 *)(p + 7) << 8;` instead of `id = (u16)(a | (b << 8))`, and `id` passed to the callee.

Measured: any one of the three edits alone, and two of them, stay at 105 (or 110-125); only the three together reach 0.
The cc1 dump shows the one-statement form is narrowed to `HImode` ior, and combine drops the `zero_extend` of the test
and the argument. A matched sibling (an audio function whose `id` is set in both arms of an `if`) keeps the `andi` the
same way: the halfword local has two sets. Hypothesis, not confirmed in the compiler's source: combine's `nonzero_bits`
trusts the last set only for a pseudo with a single set.

Failed: `(u16)` casts of any spelling, `s16`/`u32`/`int` locals, `& 0xffff`, a `u16` local alone, passing the argument
alone, `if (c) x = e; else x = e;` with a vanishing condition (reached the andi at 60, but the surrounding load
order differed and it is scaffolding), reusing the variable for the call's result (135).

Controlled: no. One function; the library family `halfword_byte_pair_split` (decomp-repair) offers the combination as
one child and reached 0 from the saved best.
