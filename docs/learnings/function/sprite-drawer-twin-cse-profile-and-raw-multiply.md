---
title: a 653-instruction sprite drawer, the twin of a matched-looking one: profile, macros and statement sinks
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32 (the function's profile carried -fno-cse-skip-blocks)
evidence: observation
date: 2026-11-23
---

The 4-bit-or-8-bit twin of the drawer in sprite-tile-drawer-gbi-macros.md (texture load, a grid of scissored texture
rectangles, hi-res and lo-res arms). Saved best 4837 (an m2c-shaped rewrite with its own `MAXS`/`ADJ`/`HIRECT` macros),
exact judge, isolated; the recorded score in the app went 4837 -> 750 -> 690 -> 645 over the steps below and the isolated
score under the profile without `-fno-cse-skip-blocks` is 325 (nine differing rows of 653).

Steps in order, with the score they reached (isolated, the function's own profile until step 4):

1. The final packet's `(s16) dsdx, (s16) dtdy` arguments written plain, the cast kept inside the macro's ADJ and multiply
   (4837 -> 3302): the value stays one register from the entry to the loop, as the target's `$s5`/`$s6`. Each of the four
   casts alone is worse (5348-5553), all together 3302. decomp-repair `macro_cast_argument_move_all` (PR 1496).
2. `dsdx = 0x400;` before the `if (hires) dtdy = 0x200; else dtdy = 0x400;` set in both arms (3302 -> 3052): jump.c's
   "x = b; if (...) x = a" conversion plus sched1 left the `li` first in its block, where reorg stole it into the previous
   branch's delay slot. decomp-repair `arm_constant_pair_statement` (PR 1469).
3. `-fno-cse-skip-blocks` dropped from the function's profile (3052 -> 2742; 750 -> 430 on the source of step 4): the
   texture-load macro reads `spr->w` before and after a `MIN` clamp written as `?:` and `x ? x : 1`, which cse reuses only
   when it may skip those blocks. decompdecomp `kmc_cse_skip_probe` (PR 3107). Not applied to the project: a suggestion needs
   the function to match under it.
4. The matched sibling's source as the model (2742 -> 430): its SDK macros (`gSPScisTexRect` with the y condition and
   correction as separate arguments, `MAX`/`MIN`), `ROWS`/`COLS` read through `*(u32 *)&spr->h`, the shadow locals and the arm
   copy. No family: the sibling's source is the evidence, the twins feature's job.
5. `reload_temp_inline` (430 -> 370, found by `prove_chain --families all --children 800`): the `ny` local written at its uses.
6. Operand order of one sum and `(s16)` dropped from two casts, found by a random hill climb over spellings (370 -> 365).
7. `s = w << 5;` before `if (flags & 0x80) { dsdx = ~dsdx; } else s = 0;` written in the then arm (365 -> 325, eight rows
   of the block and 13 -> 5 register rows overall): decomp-repair `arm_value_sink` (PR 1490). `arm_invariant_sink` needs the
   variable set once and an expression with no memory.

8. `ty = r->y + (u16)(r->TH * j);` (600 -> 595 under the function's profile, 325 -> 280 without `-fno-cse-skip-blocks`): the
   product narrowed by the cast makes the second multiply read the counter unextended, at the price of one `andi` after
   the `mflo`; with `tx = (u16)(r->tw * i + r->x + X);` as well both multiplies are unextended and the rows are
   the two `andi`s, the order of the two position adds and the arm copy (275, eight rows). Not submitted to the app: the
   same rows, a different place.

Open (nine rows): the loop counters. The target's first tests (`move s7,$zero; slt`, `beqz` on the rows word) need `u16`
counters, and its multiplies read them unextended (`mult $v1,$s2`); with `u16` counters each multiply is preceded by an
`andi 0xffff` (two rows, and the loop-back branch offsets that follow). What was measured, all with the isolated judge:

- A `u16` (or `s16`) position `tx` makes the first multiply unextended: convert.c's MULT/PLUS truncation distribution
  narrows `tw * i + x + X` into HImode when it is assigned to a narrow variable (`get_unwidened` of two unsigned halfwords,
  signedness equal), so the multiply reads the counter's low half directly. The mask then moves to the shadow copies and the
  `(s16)(t * 4)` reads: `(s16)(u16var * 4)` does not narrow (the constant 4 is signed, the variable unsigned), an `s16` one does.
  Every such typing cost 30 or more register rows elsewhere (the allocation cascade), 555 against 325.
- The same for `ty` (the second multiply) cannot keep the wide `ty + Y` the first arm reads unextended: narrow `ty` puts an
  `andi` on every read.
- `s32` counters with a `(u16)` test, or a `u16 ii = ++i` shadow, give the unextended multiply but a `blez` first test
  (the target's `beqz` comes from the HImode counter) and, for the shadow, a frame 8 bytes larger.
- `-fno-expensive-optimizations`, `-fno-force-mem`, `-fno-defer-pop` and the other flag toggles measured change nothing.
- The remaining rows besides: `(x + X) + tw*i` (the target adds the position first), and the arm copy reading the
  shadow (`move $v1,$t3`) where cse collapses it to the position.
