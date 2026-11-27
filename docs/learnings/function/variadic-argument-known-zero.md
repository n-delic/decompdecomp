---
title: variadic argument known zero
kind: function
compiler: sn-n64-281 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 174-instruction N64 results screen, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.
m2c passed a rank variable to `sprintf` in both arms of `if (rank != 0)`. The saved best (970) also declared a two-word
score table as `s32` and read it by offsets from its address.

The source change, and the measured result before and after (scores, the judge used):
App judge. The table as `u32 name[]` with a plain `if`/`else` for the rank: 970 -> 190. The first compare written
`name[0] > name[1]` so `[0]` loads first: 190 -> 160. The argument dropped from the arm where it is zero, which
removes `move $a2, $zero`: 160 -> 0. decomprepair `known_zero_argument_drop` writes the last step.

Failed experiments that would otherwise be repeated:
The ternary form of the rank was no different from `if`/`else` (both 190).

What is controlled evidence and what is one function:
One function.
