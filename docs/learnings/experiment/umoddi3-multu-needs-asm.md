---
title: libgcc 64-bit remainder, multu words need the original asm
kind: experiment
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 317-instruction runtime routine that is libgcc2.c's `__umoddi3` with `__udivmoddi4` inlined (`__udiv_qrnnd_c`, a
count-leading-zeros table). Written from the library source it went from the machine draft's 10572 to 805, and to 230
(app's judge) with the 64-bit product kept in one union and used by member.

Left: the target has `multu; mfhi $a2; sltu; mflo $a1`, two independent word registers, which is what longlong.h's
inline `umul_ppmm` asm gives (inline asm is refused by the judge). Spellings that did not do it: separate `u32` locals
from the union or from a `u64` (805 to 1400), a high-part shift plus a low word (955 to 2467, extra multiplies or
moves), inline helpers, reusing other unions, SN GCC 2.7.2 and EGCS. Under KMC GCC 2.7.2 the whole function scores
6915 or worse. About 25 spellings; not proof that no C does it.
