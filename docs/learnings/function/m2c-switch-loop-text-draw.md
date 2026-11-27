---
title: m2c switch loop around a text-drawing walk, and per-arm accumulation
kind: function
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

A 51-instruction text-drawing loop in an N64 game: per character, a glyph call and a width added to `*x`, a space
adding a constant. `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32` for this function only (the
target uses even float registers only; the brief said so).

The source change, and the measured result before and after (the app's standalone judge):

- m2c's form (a label, a `switch` whose shared tail sits in the last arm, `goto` back): 2433.
- `for (;;) { switch {..} TAIL }`, exits as `goto` after the loop (`m2c_switch_loop`): 15.
- `v = *x + K;` per arm and `*x = v;` after the switch, written `*x += K;` per arm (`switch_arm_accumulate`): 0.
- The match recorded by hand has the same loop with the pointer parameter copied into a local (`u8 *str = arg1;`)
  and the width read as `s8`; from `return` in the loop, `switch_case_order` (30) then `entry_parameter_copy` gives 0.
  `entry_parameter_copy`'s children did not compile before: in a function without locals the copy was written above
  its declaration (fixed in the library).

Failed experiments that would otherwise be repeated:

- `while (*str != 0)` and `while (1) { if (*str == 0) ... }` were rotated (1295 to 1950); only the `switch` with
  `case 0` leaving the loop kept the test at the loop head (hypothesis, no compiler source for SN's build).
- `return` from inside the loop instead of the `goto` after it: 415 to 430.
- Storing `*x = X;` in each arm without the compound form: 15, no change.
- The float read as a named pool word (`extern f32`) instead of the literal `14.0f`: worse.

What is controlled evidence and what is one function: one function. The saved best before the match read through a
pointer set before the loop and never advanced, so it was not the same program.
