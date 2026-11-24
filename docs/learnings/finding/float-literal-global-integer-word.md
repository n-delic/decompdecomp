---
title: float literal global integer word
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: an N64 function reading a float constant from the
literal pool (`lui $at, %hi(D); lwc1 $fs0, %lo(D)($at)`) where m2c wrote `temp_fs0 = D_x;` and declared `extern s32 D_x;`
because the word was assigned to a local. SN GCC 2.8.1 (sn-n64-281), `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mfp64`.

The source change, and the measured result before and after (scores, the judge used): `float_literal_global` (decomp-repair)
refused any declaration but `f32`/`float`; it now accepts `s32`, `u32` and `M2C_UNK` words when the only access in the
target is `lwc1` through `$at` or `$gp`. On the one saved best with that shape the judge's score was unchanged (1972 before
and after), so the case is not evidence that the spelling helps.

Failed experiments that would otherwise be repeated: of 837 saved bests of one project, 334 functions load a plausible pool
float through `$at` and the family already fired on 328 of them; the remaining misses were low-memory words the best does not
mention, not declarations.

What is controlled evidence and what is one function: coverage counts are controlled (read only, no judging, except the
one case); the one judged case is a single function.
