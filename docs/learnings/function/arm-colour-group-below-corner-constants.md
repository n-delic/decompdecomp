---
title: arm colour group below corner constants
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 383-instruction N64 billboard builder. A
four-arm `if`/`else if` chain on a type byte sets six `s16` corner values, four `u8` colour values, calls a getter and
sets offsets and three float literals; then a display list and four vertices. Open at 105 (one missing `move`).

The source change, and the measured result before and after (scores, the judge used), all with the app's judge:

- m2c draft 16901 to a typed rewrite (initialised `f32 scale[3]` in a block, plain `s16` corner locals the compiler
  spills into the slots the draft had as arrays, float literals): 3870.
- Colour group named after the corner values in every arm (library `arm_constant_run_shift`): 2250 to 105 on the
  otherwise final source.
- Allocation order needed one-shot `do { } while (0)` weight ladders on the third colour in one arm's override block
  (seven levels) and one around a red assignment in another: scaffolding, there to give that colour more references than
  the other two. Priorities were read from the `.lreg`/`.greg` dumps.
- An offset assigned before the getter call in three arms and after it in one.

What is left: the first arm override should be two moves (`r = g; g = b;`) before `b = 0xFF;`. cse1 deletes `g = b`
because it knows both are zero. A `do { } while (0);` after the zero initialisers keeps it through cse1, but cse2
(which ignores loop notes) then turns it into a self-copy (read in the dumps).

Failed experiments that would otherwise be repeated: `s8`/`u16`/`s32` colour types, chained zero assignments, a swap
through a temporary, XOR swap, `r &= 0`, the override as a one-case `switch` or `while ... break` (all 105 or worse);
empty fences before or after the arm's colour block (they stop sched2 moving the corner stores);
`-fno-cse-follow-jumps` (1520) and `-fno-rerun-cse-after-loop` (105, 350 with the fence) as diagnostics only.

What is controlled evidence and what is one function: one function. The live-length explanation of the group move is
a hypothesis from the dumps.
