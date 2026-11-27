---
title: A read before the parameter copy, and a constant kept across a call held by a fenced local
kind: function
compiler: SN GCC (sn-n64-281) -O2
evidence: observation
date: 2026-11-27
---

An 81-instruction N64 object update (a timer, three chained halfword scales, three chained colour bytes, a child
object spawned at the end), SN GCC `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The saved
best (105, 580 automatic trials) read its object local before assigning it from the parameter.

The source change, measured with the app's judge:

1. Read the parameter and drop the late copy: 105 -> 790 alone.
2. The colour computation after the three chained scale stores (so the timer field is re-read): 790 -> 20. The two
   rows left were `li $s2, 0x2000` and `li $v0, 0x80` swapped.
3. `u16 size; do { size = 0x2000; } while (0);` first in the arm, `size` used at every store of it, before and after the
   call: 20 -> 0. The fence is scaffolding the match relies on.

Library families: `late_param_copy_drop` (1 and 2 as one child), `arm_constant_fence_local` (3).

Failed experiments that would otherwise be repeated: a clean rewrite with a struct typedef scored 1470 (a struct
member store does not make cse forget the field, so the target's re-read never appears; pointer casts do). The size
local without the fence stayed at 20 as `s32`, `u16` or `s16`; the fenced set before the `if` scored 135 and at the
function's top 160. `prove_chain.py --families all` and `--fp-flip` found no child from 20.

What is controlled evidence and what is one function: the scores are one function. Why the fenced set stays ahead of
the other constants (the loop notes bounding its block for the first scheduler) is a hypothesis, not read in source.
