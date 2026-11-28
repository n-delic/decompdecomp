---
title: Block-local call arguments let the outer loop hoist one more constant
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 280-instruction routine that draws a 320-wide background as 8 by 10 textured rectangles: two nested unsigned loops
written `while (1) { if (i >= 8) break; ... }`, with SDK display-list macros (a texture-tile load and a texture
rectangle) in the inner body. With those macros and loops the saved score went from 18861 to 10 in a few judged
variants. The two remaining rows were the load-tile opcode word:

    target   lui $s7, 0xf400   or $s6, $t7, $s7
    ours     lui $v0, 0xf400   or $s6, $t7, $v0

The source change, and the measured result before and after (scores, the judge used):

Each of the two inner statements in a block of its own, holding some arguments in locals. App judge, 10 -> 0:

    { void *timg = D_image; gDPLoadTextureTile(gfx++, timg, ...); }
    { s32 s = x << 5; s32 t = y << 5; gSPTextureRectangle(gfx++, ..., s, t, ...); }

Why, as far as it was checked: in the target the constant was moved out of the outer loop too and then got no
register, so reload built it in `$s7`. GCC 2.8.1 `loop.c` `move_movables` moves it when
`threshold * savings * lifetime >= insn_count`, with `insn_count` doubled for every register an inner loop already
moved and `threshold` 3 lower per move. The `-dL` dump showed that condition failing by a little. With four extra
nested blocks that each declare a local, the lifetime in the dump went from 128 to 135 while the insn count stayed 107,
and the score was 0. That block notes are what lengthens the lifetime is a measured result, not read in the source.

Failed experiments that would otherwise be repeated:

Either block alone: 10. Empty `do { } while (0);` before the inner loop or after it: 10, lifetime unchanged; inside
the inner loop: 1540. Unsigned `x`/`y`, shifts for multiplies, the rectangle's second word as `x << 21`, a constant
line value: 10. The load macro wrapped in `do { } while (0)`: 4400. The opcode held in a local: 590 to 840.
`x + 32` and `y + 32` held in locals: 2250 (the copies add instructions). `j * 32` and `i * 32` written out instead of
`x` and `y`: 2995. SN GCC 2.7.2 with the first source had the same two rows.

What is controlled evidence and what is one function:

One function. The library family `loop_argument_scope` (decomp-repair) writes these blocks; run from the 10-score
source, `prove_chain.py --families loop_argument_scope` reached 0 in one step.
