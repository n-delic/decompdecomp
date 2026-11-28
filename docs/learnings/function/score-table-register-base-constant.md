---
title: Literals folded from a saved register, and a variable split by arm (score table, 15175 to 0)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 261-instruction N64 score table drawn in two layouts (`if (attract) {3D text} else {2D text}`), ten rows each, under
the flags above (the function's own profile; the brief suggested `-mfp32`, but the match needed no profile change).
The saved best was an m2c draft at 15175.

The source change, and the measured result before and after (scores, the judge used):

A plain rewrite from the target (gfx writes as two-word command macros on `gfx++`, record copies for the colour and the
scale, indexed arrays) scored 4461 with literal strings, 520 once the format strings were the target's symbols and each
arm held its own pipe sync, and 0 with the row's `y += 0xA` after the `if`/`else` (`chain_tail_hoist` already offers
that last step). The app recorded 0.

What mattered most, measured by taking it back out of the match (the app's judge run standalone):

- The x position is one variable, set to `0x5C` at entry and to `-0x5A` in the first layout, and every column is
  `x + k`. Written as the literals m2c prints (`0x84`, `0xDC`, `(f32) 0x3C`) the same source was 4806. The target's
  evidence is a saved register loaded only with constants and read by `addiu $aN, $s6, k`.
- The y position is one variable for both layouts. A second one for the first layout (m2c's `var_s3_2`) was 405.
- m2c's `goto` ladder for `a && b` made no difference (0).

Library families `register_constant_base` and `idle_arm_local_merge` do the first two: 5211 -> 405 -> 0 from the match
with both spellings reverted.

Failed experiments that would otherwise be repeated:

The first family's default placement (each assignment just before the loop) scored 60, and with the entry assignment
ahead of the counter's `i = 0` it scored 270: the order of the `li`s follows the source's order of the assignments, so
the family offers several placements. `gbi_macro_commands` offered no child for the saved best, whose cursor locals
are typed `s32 *` with byte offsets (not investigated).

What is controlled evidence and what is one function:

One function. Neither family reaches 0 from the saved best: applied to it, the register family gave 13385 and the
merge 15175. The display-list and indexing spellings of the m2c draft still have to be rewritten first.
