---
title: return type widen sine lookup
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function looked like: a 28-instruction four-quadrant table lookup (`lhu` from one of three halfword tables, `negu` on two quadrants), m2c draft `u16 f(s32)` with `return (u16) -(s32) *(u16 *)(...)`. KMC GCC 2.7.2, -O2.

The source change and result: `s32 f(s32)` with the `(u16)` casts of the returns dropped. Judge 100 -> 0. The one differing row was `andi $v0,$v0,0xffff` after the `negu`; the target has no narrowing of `$v0` anywhere.

Failed experiments: widening the return type alone (casts kept) stays 100, the cast still masks. Library family: decomp-repair `return_type_widen` (children `return_type_widen`, `return_type_widen_type`), offered only when the target never narrows `$v0` (andi 0xff/0xffff, sll 16/24).

Evidence: one function; the family reproduced 0 from the pre-match saved source with the app's judge.
