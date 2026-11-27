---
title: A turning routine's angle wrap loop and its two-sided turn condition
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 113-instruction N64 routine that turns an object towards a target by at most a given rate: the wanted angle and the
object's own are wrapped into range by loops, reduced `% 0x1000`, and the difference picks a turn direction. Flags:
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The saved best (m2c with search edits) scored 2140.

The source change, and the measured result before and after (scores, the judge used):

- `angle = E; while (angle < 0) { angle += 0x1000; }` (`s16`) copies the test ahead of the loop (620 in otherwise exact
  C). `u16 t = E; while ((angle = t) < 0) { t = angle + 0x1000; }` gives one test at the head (0). Family
  `wrap_loop_test_assignment`.
- `else if ((diff < 0 && diff >= -0x7FF) || (diff > 0x800))` scored 1786 in otherwise exact C; the same condition as
  nested `if`s and `goto`s scored 0. Family `compound_or_goto_ladder`. The two together take the state with neither
  edit from 2396 to 0.
- Also needed from the draft: `%= 0x1000` on the angle in place of m2c's shift temporaries, one `s16` angle for both
  stores, and `absS32(diff) > rate` (existing family `call_first_compare`).
- Submitted to the app: 2140 -> 465 -> 365 -> 0.

Failed experiments that would otherwise be repeated:

- An `s32` twin (970), an `s16` twin (620) and `t += 0x1000` (145) in the wrap loop.
- A `static inline` wrap helper (365 at best: the helper's variable gets its own register).
- A `goto` loop with `angle = t;` at its label (205 to 470).
- Other spellings of the second condition without branches (casts, `<= -1`, swapped operands, `!(a >= 0 && a < K)`):
  1986 to 2076. A conditional expression `diff < 0 ? diff >= -0x7FF : diff > 0x800` reached 665.
- The project's KMC GCC 2.7.2 profile was farther from the target than the SN profile on every clean candidate tried.

What is controlled evidence and what is one function:

All scores are one function's, from the app's judge. Why the branch form stays apart (cse not reaching a label two
branches enter) is a hypothesis from the dumps, not read in a compiler source.
