---
title: range test bounds in narrow locals, and a narrowed value compared with a load
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An 81-instruction N64 object routine: `lo(w) < x && x < hi(w)` on 16-bit angles, then `(s16) (w + 0x4000)` against a
field read again. The target loads `0x4000` before the first test's `beqz` and subtracts in its delay slot; ours built
the whole second bound after the branch.

Source changes from the saved best (isolated judge, which the app's judge confirmed):

1. `s16 low = w + 0xC000; s16 high = 0x4000 - w; if (low < x && x < high)`: 540 -> 60. Only `high` in a local: 735.
   Family `range_bound_locals`.
2. `(s16) v < M2C_FIELD(p, s16 *, 0)` written `M2C_FIELD(p, s16 *, 0) > (s16) v`, so the load comes before the shift
   pair: 60 -> 0 (alone 540 -> 490). Family `computed_compare_flip`.

Failed: a `max = 0x4000` local (`s16` or `s32`, any statement order) compiled identically (540, the constant is
propagated); assigning `x = obj->x` before or after `w` changed nothing.

One function. That the scheduler then sees both bounds in one block is a hypothesis, not read in the compiler source.
