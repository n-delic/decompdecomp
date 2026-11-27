---
title: A shared exit written as one early return, with a byte flag widened
kind: function
compiler: SN GCC 2.8.1 -O2
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 62-instruction N64 spawn timer, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The saved
best (m2c lineage, 35 attempts) scored 690. It left a shared "reset the timer and return" exit in two forms: the first
test held a copy of it (`if (busy) { o->timer = 0; return; }`), the later tests jumped to a label after the work
(`goto block_7;`). The target has that exit once, before the work, and branches over it.

The source change, and the measured result before and after (scores, the judge used):

1. All the tests joined as one early return, `if (a || b || c) { o->timer = 0; return; }` and then the work:
   690 -> 100. decomp-repair `goto_tail_early_exit` (a guard that already holds the tail now joins the exit).
2. `u8 flag = gFlag;` widened to `s32 flag`: 100 -> 0 (the u8 copy adds `andi $v0, $v0, 0xff`). decomp-repair
   `narrow_widening`, which already did this.

Scores are from the app's own judge worker run standalone.

Failed experiments that would otherwise be repeated:

Dropping stray call arguments, caching the twice-read level global, and merging only the conditions into the outer
`if` (leaving the exit after the work) each left the score at 690. Replacing the byte flag with a direct read of the
global in the condition scored 425.

What is controlled evidence and what is one function:

One function. The family's behaviour is covered by its test in the library.
