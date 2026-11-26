---
title: Loop-invariant key written once before the loop, fences removed
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 29-instruction list walker (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`). The machine draft compared each node's
`s16` field with `(s16) (M2C_FIELD(arg0, const u16 *, 0x216) + 0x64)` written inside the loop, with `do { } while (0)`
fences round the head load and the cursor step. Score 120 (frame `addiu $sp`, the key's `lhu` and the head's `lw` in
the wrong order).

The source change, and the measured result before and after (scores, the judge used):
`s16 key;` set once as the function's first statement (`key = (s16) (...);`, above the head load and its guard), the
loop compares `temp == key`, every fence removed: 120 -> 0 in the app's judge. The hoist alone and the unwrap alone
did not move the score; the pair is one child. Library family `loop_invariant_hoist_unfenced`
(`loop_invariant_expression_hoist`, also `loop_fence_unwrap`), proved by `prove_chain.py` from the pre-match draft in
one step.

Failed experiments that would otherwise be repeated:
The other placements the family offers (key right before the loop, or before the top-level statement) were not scored
separately; the chain took the first-statement placement.

What is controlled evidence and what is one function:
One function. The reason (the key's load and `addiu`/`sll` ordered before the head's load) is read from the rows, not
from the compiler's source: a hypothesis.
