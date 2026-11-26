---
title: pointer byte difference cast
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: three same-shaped setup routines
(`osInvalICache`, `osInvalDCache`, a copy call and a `bzero` over four address ranges), each with a range end typed by
m2c as `s32 *temp` and its length passed as `temp - (s32) &start`. `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`.

The source change, and the measured result before and after (scores, the judge used): `(s32) temp - (s32) &start`.
`p - (s32) x` is pointer arithmetic, so the integer is scaled by the element size (`sll 2` before the `subu`) and the
target (a plain `subu` of the addresses, no `sra`) is a byte difference. All three routines went 210 -> 0 (fast_judge).
The other three length arguments of each routine have `char` operands, which are not scaled: they compile the same with
or without the cast. Library family `pointer_byte_difference` (decomp-repair: `pointer_byte_difference.py`), offered for a
wider-than-byte pointer or array minus an integer-cast address, one child per site and one with all of them;
`prove_chain.py --families pointer_byte_difference` reaches 0 from the saved pre-match source of each routine in one step.

Failed experiments that would otherwise be repeated: none. No existing family covered it (`prove_chain --families all
--children 400` stayed at 210): `pointer_scale_repairs` rewrites byte steps, not a subtraction.

What is controlled evidence and what is one function: observation on three functions of one game with the same source
shape; the target having no `sra` is the hypothesis's support, the judge decides each child.
