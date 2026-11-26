---
title: sign bias shift as signed divide
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

A 5-instruction N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) made one call whose second argument
was half of an unsigned-typed global. The machine draft spelled it as m2c reads GCC's expansion:
`(s32) (D + ((u32) D >> 0x1F)) >> 1`.

Source change: the same value written as the signed division, `(s32) D / 2`. Exact judge, 15 -> 0. The remaining rows
were the register of the loaded global (`$a1` in the draft, `$v0` in the target): the division's expander reads its
dividend once, where the written-out sum reads it twice.

Family: decomp-repair `sign_bias_shift_divide` rewrites `[(s32)] (X + ((u32) X >> 31)) >> K` (addends in either order)
as `(s32) X / 2^K`. Run on the pre-match source it gives the exact source in one child (15 -> 0, fast_judge).

Not covered: the two-statement form (`t = x + ((u32) x >> 31); ... t >> 1`) and a dividend that is a call or an
expression other than a name with members and subscripts.

One function; the rewrite is the compiler's own division, so the rule is general, but only this case was measured.
