---
title: A pool search written with byte cursors: record, loop, guarded reread
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 search over a fixed pool of objects (147 instructions) returning the first one in a kind range that overlaps
the caller's object. SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The saved best
(score 2327, 62 attempts) walked the pool with two `char *` cursors, one 0x24 past the other, both stepped by the
object size 0x88, in a `goto` loop. The target's far cursor is the loop pass's own: one record pointer gives it back.

The source change, and the measured result before and after (scores, the judge used):

Judged with the app's standalone judge, each family applied to the previous result:

| step | family (decomp-repair) | score |
|---|---|---|
| saved best | | 2327 |
| record built from both cursors' offsets and the stride, and the loop as `do`/`while` | `strided_record_loop` | 1730 |
| the field tested, copied and tested again read through a `static inline` getter | `guard_reread_getter` | 1435 |
| the search's result variable written as returns | `found_value_return` | 1365 |

The match itself (0) was a rewrite by hand from the target and the matched neighbours' style: the record, a
`for` loop with a `u16` counter and the pointer stepped in its header, `kind` and the squared deltas in locals, an
early `return NULL` for the first guard, and the getter. The rewrite without the last two scored 530, with the getter
in nested `if`s 200, with the early return too 0. The chain above does not reach 0 by itself.

Failed experiments that would otherwise be repeated:

- Folding the derived cursor into its base with the `goto` loop kept: 4814 (worse). The `while` form alone: 4280.
- A `volatile` read for the second load: the right load, plus an `andi` (100).
- The getter returning `u16`: no gain (the chain stayed at 1730). It returns `int`.
- The second read as a plain copy, through `do { } while (0)` placements, or as a ternary: cse reuses the first load.

What is controlled evidence and what is one function:

All of it is one function. Why the inline getter keeps the second load is a hypothesis (its labels end the extended
block cse reads across), not read in the compiler's source.
