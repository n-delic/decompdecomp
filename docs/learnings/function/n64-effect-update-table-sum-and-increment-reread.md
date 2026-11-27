---
title: unknown-table pointer sum and increment through a temporary in an N64 effect update
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 157-instruction effect update of an N64 game: it counts a timer, and at one tick spawns pairs of objects whose
position offsets come from a small `u16` table. The machine draft (saved best 2635) used the per-function profile above.

The source change, and the measured result before and after (scores, the judge used):

- The draft read the table as `*((i * 4) + base)` with `base` an `M2C_UNK *` to the symbol, so the offset was scaled
  twice (`sll 4`, `lw`). The table as `extern u16 D[]` read `D[i * 2]`: 2635 -> 1845; `*(u16 *) ((u8 *) base + (i * 4))`:
  2635 -> 1975. decomp-repair `word_table_pointer_sum`.
- `t = p->timer + 1; p->timer = t; ...; p->frame = p->base + (t >> 2);` as `p->timer += 1; ... (p->timer >> 2)`:
  445 -> 0 as the last step. decomp-repair `field_increment_reread`.
- A clean rewrite also matched. There, a `for (;;)` whose exit was `goto done` (not `break`) kept the exit test at the
  top (1570 -> 970), and swapping adjacent member stores to the target's order closed the last rows.

Failed experiments that would otherwise be repeated:

- A `goto` loop (`loop: if (...) { ...; goto loop; }`) scored worse (2663): the loop pass no longer hoisted the table
  address and the constant.
- `(u16) i * 2` element indexing of the table on top of a half-repaired draft scored worse (2005) than the byte form;
  it paid off only once the table was declared as an array.

What is controlled evidence and what is one function:

All of it is one function. That the named temporary's own pseudo is why the increment spelling matters is a hypothesis.
