---
title: record field copy first, constant regrouped, store fenced before a call
kind: function
compiler: SN GCC 2.8.1 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 112-instruction N64 object update: a counter's early return, a few calls that fill fields, then a second record
allocated and copied from the first. The saved best was the m2c draft, 595 after the app's repair trials. Differing
rows: a store and the argument copy round a `jal` in the other order, `addiu` on the other operand of a `subu`, two
loads placed early.

The source change, and the measured result before and after (scores, the app's judge):

Each edit alone against the 595 draft, then the greedy chain of the library families (595 -> 385 -> 180 -> 60 -> 0):

- `(s32) ((h + 0x200) - (random() >> 5))` -> `h - ((random() >> 5) - 0x200)`: 595 -> 385 (`constant_term_regroup`).
  `(h - (random() >> 5)) + 0x200` is not the same: with the other three edits it stood at 60.
- the new record's one field copy (`o->scale = parent->scale;`) moved nine statements up, to the head of its block,
  before the constants: 595 -> 390 (`field_copy_to_head`). Grouping every copy first (`field_copy_group`) gave 965.
- `t = parent->field80;` moved down, past a call, to just before the `if (t == K)` that reads it: 595 -> 535
  (`assignment_at_use`, which offered nothing while it took `M2C_FIELD(` for a call).
- `do { o->scale = v; } while (0);` for the store right before `next(o)`: with the other three, 120 -> 0
  (`fence_store_before_call`). The target stores before the `jal` and puts the argument copy in its delay slot.
  This is scaffolding.

Failed experiments that would otherwise be repeated:

- a ternary in the store (`o->x = v ? v : K;`): 300.
- the store written inside the next call's argument with a comma: 120, no change.
- `local_weight_ladder` children that fence the store also fence dozens of other statements: 1250 and worse.

What is controlled evidence and what is one function:

One function. The families have unit tests on small sources; why the scheduler behaves so was not read in the
compiler's source (hypothesis).
