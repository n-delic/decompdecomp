---
title: narrow callee return shifted u32
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -O1 -g
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 2,000-byte-class N64 object initialiser (KMC GCC 2.7.2 -O1 -g) calling a random-number helper whose m2c prototype was u16 and whose result is shifted right and stored to halfwords.

The source change, and the measured result before and after (scores, the judge used):
Four extra andi $v0,$v0,0xffff, 400 -> 0 by declaring the helper u32 (the matched callee's own prototype). s32 gave 600: the target shifts with srl. Another draft of the same function also lacked the callee's unused second parameter (410 -> 400 by unused_parameter_insert). decomp-repair callee_return_wide now offers u32 as well as s32, and reads prototypes written with an empty parameter list and under m2c's #ifndef preamble.

Failed experiments that would otherwise be repeated:
s32 return (srl became sra, 600).

What is controlled evidence and what is one function:
One function; the u32 variant and the empty-list/preamble reach are tested in tests/test_callee_return_wide.py.
