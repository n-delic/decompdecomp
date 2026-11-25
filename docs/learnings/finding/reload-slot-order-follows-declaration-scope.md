---
title: A spilled float's slot follows the pseudos created before its declaration
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 920-instruction N64 segment-against-triangles test (SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp32`) differed in one thing: a float `sign` (set to -1.0f or 1.0f, then multiplied into three edge tests)
was stored at `0x15c($sp)` where the target had `0x168($sp)`. The target's order was: `t` at 0x158, the loop's hoisted
address of the vertex array at 0x15c, an 8-byte double pseudo at 0x160, and `sign` at 0x168.

`sign` gets no hard register in global.c. Reload then gives every such pseudo a slot in pseudo-number order
(`reload1.c`, `for (i = LAST_VIRTUAL_REGISTER + 1; i < max_regno; i++) alter_reg (i, -1);`, read in the gcc-2.8.1-pm
source). A local declared with the function's other locals gets its pseudo when the function starts, which is a lower
number than the pseudos loop.c and expansion create later. Declared at the start of the `else { ... }` block that sets
and reads it, it gets a number after them.

The source change, and the measured result before and after (scores, the judge used):

`f32 sign;` moved from the function's declarations into the start of the `else` block: 7 -> 0 with the app's
judge-worker (dump: `cc1n64 -dl -dg` under wibo; `.greg`'s spill messages and `(const_int 348)` slot references). The
library's `declaration_scope_sink` already had this child but did not offer it. Its `LIMIT = 8` was used up by twelve
`Vec3` struct locals and by `for`-body locals that its loop check never matched (a `for` head has `;`). Fixed in
decomp-repair #1745, which ranks scalars ahead of struct locals and recognises `for` heads.

Failed experiments that would otherwise be repeated:

Before this, `sign` was tried at every position among the top-level declarations, as a ternary, if/else or negated
form, with both orders of `sign * dot`, with do-while weights, and in 160 random declaration orders. None went below 7:
top-level order cannot move a pseudo past the ones created later.

What is controlled evidence and what is one function:

The slot order is read from the compiler source and the dumps (controlled). The 7 -> 0 is one function. The same
source then matched its 1292-instruction twin.
