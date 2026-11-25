---
title: texture strip blitter frame local and reused extents
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

An N64 texture-strip blitter (477 instructions, SN GCC 2.8.1 with the flags above): it checks that the destination
rectangle is at least 3x3, computes two 10.10 scale steps with `div`, sets combine and other modes through three
callees, then a `switch` on the image depth loads the texture in strips of 0x800 (16-bit) or 0x200 (32-bit, one row
overlap) texels with `D_80177E18++` display-list macros. Its two look-alikes are matched (`similar.py`), but this one
needed different source to get the target's frame and saved registers. The saved best started at 6317 (frame 0x50
against the target's 0x58, `$fp` holding the wrong value). Ended at 525: only register rows left.

The source change, and the measured result before and after (scores, the judge used):

All scores are the app's standalone judge (`fast_judge.py`), confirmed with `setup.py try`; each row is one edit on the
previous state.

| edit | score |
| --- | --- |
| saved best | 6317 |
| `h = y1 - y0 + 1;` after the guard, not inside it: the target copies the guard's value into a second pseudo | 6221 |
| `img->width` read inside both divisions and `texw = img->width` after them: cse makes `texw` the copy (`move $s0, $a1` in a `jal` delay slot); the frame becomes 0x58, the score rises while the differing rows fall (212 -> 201) | 6710 |
| `offset += texw * (n << 1)` (the target shifts `n`, not `texw`) | 6555 |
| the constant scale local `yscale = 1;` assigned before the calls: it crosses them, gets no saved register, and reload rematerialises `li $t8, 1` at each use | 5000 |
| `strip = n * (h << 2) / texh` inside the `if`, and `hh = h << 2` at the 32-bit loop's top: loop.c hoists it and reload spills it to 0x2c through the dying `h` register | 4795 |
| `(void)&dsdx;`: the second scale step is a frame variable, loaded into a dead parameter's `$fp` before the switch's `beq` and read from 0x24 again in the other arm | 5142 -> 2132 |
| `rows = texh = img->height;` | 1815 |
| `rows -= n - 1; n--;` (the target computes `rows + 1 - n` through one register) | 1470 |
| the width extent reused: `w = x1 - x0 + 1;` compared, then `w = img->width;` used by both divisions and `texw = w;` (so `x1 - x0 + 1` is recomputed for the second division, and `x1 - x0` is shared) | 1370 |
| the height extent reused the same way: `texh = y1 - y0 + 1;` compared, `h = texh;`, then `texh = img->height;` before the first division | 1245 |
| statement order bpp, w, dsdx, h, texh, dtdy, pixels, rows, texw (a 12,960-order permutation) | 1195 |
| a block-local strip counter for the 32-bit case (`case_temporary_nested_split`) | 1125 |
| `h = y1 - y0; texh = h + 1;` (the difference in block 0, the `+ 1` beside the guard; `sum_shape` found `texh = y1 - y0; texh += 1;` at 600) | 525 |

Failed experiments that would otherwise be repeated:

- `volatile s32 dsdx` (6850) and `s32 dsdx[1]` (4602): a volatile read is never hoisted out of the second arm's loop, and
  an array is never read through a register; only the address-taken scalar gives the target.
- `sx = dsdx;` before the switch with a plain register `dsdx`: cse replaces the copy (identical output).
- Reusing the `y0` parameter for the scale step (cse folds it while `dsdx` is a register).
- Remaining rows (both loops): the strip counter gets `$a3`/`$t1` where the target has `$a2`, and the CALC_DXT
  `texw / 4` rounding temporary takes `$a2`. In the `-dg` dump their global.c priorities are 0.452 (counter, 14 refs
  over 93 insns) and 0.462 (temporary, 6 over 26): one step of live length or one reference flips them. No change of
  the loop tail order, the counter's declaration, scope or initialiser, a `MIN` macro, the order of the F3 word's
  operands, 400 random declaration orders or `do { } while (0)` fences (worse) moved it, nor `prove_chain --families
  all --children 2000 --combine 8`.
- The first block's `y1 - y0` goes to `$s3` (h's pseudo) where the target uses a temporary `$v1`.

What is controlled evidence and what is one function:

One function. The library has the frame-variable step as `address_taken_local` (decomp-repair 0.6.19), whose child on
the function's best was exactly the hand edit (4602 -> 1125). The extent reuse (a guard's local given the later field
read, 1470 -> 1370 -> 1245) is not a library family yet: `dead_local_stage` found a weaker reuse (1285). That cse's
knowledge of a frame read stops at the switch's first arm is the reading of the measured output, not confirmed in the
compiler source.
