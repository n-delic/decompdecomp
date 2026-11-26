---
title: textured strip drawer, the call reading an update, and two command-stream loop constants
kind: function
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Five functions of an N64 game re-split by Check again, SN64 GCC 2.8.1 with the project's flags. Three (22, 48 and 51
instructions) had drafts in which every in-function branch was drafted as a return: the disassembler printed each
target as `. + 4 + (N << 2)` and `seeds.cut_outside_branches` sent them all to a returning label (fixed in #3702: an
in-function target gets its own label). Read by hand, two were sibling `if`s m2c had nested and one had `a || b` drafted
as `!a && !b`; each matched at once (10, 940 and 2881 -> 0).

A 360-instruction textured-strip drawer: a gDPLoadTextureBlock-style load per strip of a 32-bit or 16-bit image (its
matched sibling, `drawImage`, has the same command words), then a draw call per strip. A 188-instruction F3DEX2
command-stream compiler: `switch (*cmd++)` over an `s16` stream, gSPVertex / gSP1Triangle / gSP1Quadrangle /
gSPEndDisplayList, a vertex-alpha store in a counted loop.

The source change, and the measured result before and after (scores, the judge used):

Strip drawer, app judge (`fast_judge.py`, confirmed with `compile_and_diff`): a rewrite from the asm 1326; the image
fields read in the draft's order 936; `x0 = -(size * 2); x1 = size * 2; step = (size * 2) / h;` instead of `x1 = size * 2;
x0 = -x1; step = x1 / h;` 746 (the target keeps a copy `move $fp, $v0`); the draw call reading the new value,
`draw(x0, y, x1, y + step * rows, ...); y += step * rows;` instead of `y0 = y; y += step * rows; draw(x0, y0, x1, y, ...)`,
in both strip loops 126 (one loop alone 651 or 1682: only together); `u8 *data` declared before `step` (two spill slots,
0x74/0x78, follow the pseudos' creation order) 120; `off = 0;` written after the first call
(`func_80024FE8(rot); off = 0;`) instead of before the first display-list command 0: sched1 then puts the zero move
between the command's load and its stores, as the target has it. decomp-repair `call_arg_late_update` offers the 126
step from the 746 state since RetroReversing/decomp-repair#1804.

Command-stream compiler: cases in the target's order (0x7013, 0x7003, 0x7004, 0x7012, 0x7000) 800; the last rows were
the quadrangle's `flag == 2` constant, which the target hoists into `$t9` and ours rebuilt twice. loop.c's dump said
"savings 2 not desirable": `threshold * savings * lifetime >= insn_count` with `threshold` dropping by 3 for every
constant already moved (loop.c, `move_movables`). The vertex alpha written as a `u8 alpha = 0x80;` local at the
function's head, before the counter, is not a movable (set outside the loop), so one fewer constant is moved first and
the 2 still passes: 800 -> 0 (the target's `li $s0, 0x80` is the function's third instruction, before the loop's
hoisted constants, which is the sign).

Failed experiments that would otherwise be repeated:

- Strip drawer: four spellings of the `y` update with a staged `y0` (`y = y0 + step * rows`, operand orders) compiled
  identically (746); only moving the update after the call changed it.
- `buildObjectMatrix(obj, mtx, off)` for its literal 0 argument: no change (cse folds it back to 0).
- Command compiler: `s16` locals for the vertex indices (5795), a `two = 2` local (2075), the end case after the loop
  via `goto` (2830), the alpha local declared after the counter (205).

What is controlled evidence and what is one function:

Each score is the app's judge on one function; the loop.c threshold reading is from gcc-2.8.1-pm's loop.c source and the
loop dump of our candidate (SN's own build has no source). The draft-branch fix is tested in
`tests/test_ido_drafts_compile.py` with a homemade function.
