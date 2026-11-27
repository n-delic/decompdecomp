---
title: A halfword step held in a saved register, and two range tests kept apart by a copy
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 231-instruction N64 steering routine of a seeking shot that spawns linked objects, matched through the app's judge.

The source change, and the measured result before and after (scores, the judge used):

- The target loads a constant into a saved register early (`addiu $s3, $zero, 4`) and reads it with
  `andi $a0, $s3, 0xffff`. m2c writes each read as `t = 4 & 0xFFFF;`, which folds. `u16 turn = 4;` read at every
  site took the saved best from 4253 to 3045 (decomp-repair `held_mask_constant`).
- A clean rewrite with a typed record scored 2498. Moving one store group into the target's order: 2233.
- `if (turn < abs(diff))` as `if (abs(diff) > turn)` in both arms: the call runs first and the masked constant lands
  in `$a0` (`call_first_compare` offers one arm per child; each step improves).
- `(diff < 0 && diff >= -0x7FF) || diff > 0x800` was folded into one unsigned range test. The target keeps two
  `slti`. Either `s16 diff2 = diff;` read by one side (the match as submitted), or the condition as a goto ladder
  (`compound_or_goto_ladder`), keeps them apart.

From the clean rewrite the library's own families reach 0 (`compound_or_goto_ladder`, `independent_statement_swap`,
`call_first_compare`). From the m2c draft they do not: after `held_mask_constant` a capped chain gained 30 points.

Failed experiments that would otherwise be repeated: from the draft alone, writing the two sign-bias remainder blocks
as `% 0x1000` (4253 to 4403) and dropping the stray second argument of the absolute-value call (4328) each scored
worse; with the held local the remainders were still worse (3045 to 3193).

What is controlled evidence and what is one function: one function. That the copy defeats the front end's range
folding is a hypothesis, not confirmed in the compiler source.
