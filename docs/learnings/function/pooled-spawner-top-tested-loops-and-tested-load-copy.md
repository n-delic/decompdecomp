---
title: pooled spawner top tested loops and tested load copy
kind: function
compiler: sn-n64-281 -O2
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: four object handlers of one N64 game
(207 to 290 instructions), `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, two of them with a
particle-pool spawn loop (find a free slot, `i = 501; break;` when the pool is full) inside a fixed-step ray loop.

The source change, and the measured result before and after (scores, the judge used), all with the app's judge:

- A loop whose test stays at the top, closed by `j` with the counter step in its delay slot, was written
  `i = 0; while (1) { if (i >= N) { break; } ...; i++; }`. A `for` rotates the test to the bottom and keeps the
  position locals hoisted before the loop in the wrong registers: 2215 -> 560 on one handler, and 2338 -> 8 on another
  (both loops). A `goto` loop was worse (2990, 2985): its body is not weighted as a loop.
- Positions read once before the loop as `u16` locals (`x = obj->x;`): 3007 -> 2215. Their stack slots follow
  declaration order: declared after the two `s32` step locals, 8 -> 0.
- `v = obj->head; if (v == NULL) { v = walk; }` where the target loads `$v0`, branches and copies to `$s0` in the delay
  slot: `h = obj->head; if (h == NULL) { v = walk; } else { v = h; }`, 210 -> 0. Now decomp-repair
  `tested_load_else_copy`.
- A value added to a loaded halfword in the same register: one block-scoped `s32` local per arm,
  `s32 f = o->frame; ...; e->a = e->b = f + 3;` (140 -> 0 against a shared local).
- `x % 2 == 1` compiles to `(x / 2) * 2 == x - 1`; writing the latter by hand reuses the range test's `x - 1` (160 -> 50).

Failed experiments that would otherwise be repeated: for the tested load, `if ((v = obj->head) == NULL)`, a `?:` with
an inline tail walk and an `else` written first (`if (h != NULL) v = h; else ...`) all stayed at 115 to 615.

What is controlled evidence and what is one function: each step above was judged alone against its parent. The loop form
held on two handlers; a third took the same source shape from its sibling and needed only the pointer reuse of the
last block (30 -> 0).
