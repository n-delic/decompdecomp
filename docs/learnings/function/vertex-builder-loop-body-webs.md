---
title: vertex builder loop body webs
kind: function
compiler: SN64 GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 544-instruction N64 billboard/trail builder, `sn-n64-281` with `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp64`. Its vertex loop writes three `Vtx` records per point (`&D[k++ + base]`, `ob[]` from two `s32` tables, `cn[]`
colour bytes 0xFF/0/0x80). The saved best (1350) used one block pointer `Vtx *v = ...; ... v = ...; ... v = ...;` and
kept the 0x80 alpha in the loop through the vertex count local (`nv = 0x80; v->cn[3] = nv; ... nv = 0; v->cn[3] = nv;`,
then `nv = k;` after the loop: a local set twice in the loop is not a movable, loop.c `count_loop_regs_set`). The target
has the three vertex pointers in `$v1`/`$a0`/`$a1` (the next address computed early), the 0x80 in `$v0` inside the loop,
`k` in `$t0` and a `move $t6,$t0` count copy after the loop.

The source change, and the measured result before and after (scores, the judge used):

Two web splits in the loop body, then three existing library steps (decompdecomp `fast_judge`, the app's judge):
- one pointer per vertex record (`v`, `v2`, `v3`): 1350 -> 1395 alone;
- a block-scoped local for the alpha constants instead of the count (`u8 alpha; alpha = 0x80; v2->cn[3] = alpha; ...
  alpha = 0; v3->cn[3] = alpha;`): worse alone; both together 1955, with the 0x80 in `$v0` in the loop, `k` in `$t0`
  and the count copy where the target has them;
- `store_constant_web` (v2's `cn[1]` 0xFF and v3's `cn[1]` 0 through one `value` local): 1520;
- `statement_slide` (`value = 0xFF;` two statements up, before `v2->ob[2]`): 600;
- `dead_scratch_reuse` (the quad loop's second vertex index written into `k`, which makes `k` long-lived and lifts it
  above the CD170 giv): 530 (submitted and recorded). decomp-repair `loop_body_web_split` (#1769) offers the two splits
  alone and together; from its combined child `prove_chain` reaches 530.

Left at 530 (16 register rows): the target loads one 0xFF into `$a2` in the preheader and uses it for every 0xFF byte;
ours has `value` (block-local, `$a2`, an in-loop `li 0xff`) plus a hoisted 0xFF constant in `$t3`.

Failed experiments that would otherwise be repeated:

- A static inline vertex helper (`set_vtx(v, x, y, z, r, g, b, a)`): 7605 (`s32` parameters, word loads) and 6055
  (`s16`/`u16` parameters); argument evaluation reorders the loads.
- Dropping the stray `last` argument of `func_8001EAD0`: 1615 (the target really computes `count - 1` into `$a0`).
- Extra flags on the saved best: `-fno-rerun-cse-after-loop` 1815, `-fno-schedule-insns` 4785, `-fforce-addr` 1775;
  `-frerun-loop-opt`/`-fmove-all-movables`/`-f[no-]reduce-all-givs` are not accepted by cc1n64.
- From 530: one `value` set once in the loop and used for every 0xFF (1176), `value = 0xFF` before the loop (2565) or at
  the loop top (2370), a `u8 col` user variable for the 0xFF stores (2825), `v3->cn[1] = 0` literal (2525); all
  library families, 294 children: nothing below 530.
- The 0xFF constant cannot outrank the CD350 giv by priority alone (7 uses: 15 weighted refs, live length doubled by
  local-alloc's REG_EQUIV rule, 0.346 against 1.508): the target's order needs a conflict that keeps `$a2` from the
  three loop pointers but not from the constant.

What is controlled evidence and what is one function:

One function; the scores are the app's judge. The allocator readings are hypotheses checked against the `-da` dumps
(`passes.py`), not a controlled test.
