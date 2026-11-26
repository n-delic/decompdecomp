---
title: The default before an inverted test, with a fence so cse keeps a pointer register
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function looked like: a 59-instruction N64 state update. In one block the target sets a flags word to 0 first,
branches with `bnez` with the default argument in its delay slot, and reads `lhu $v0, -4($s1)` off a saved pointer.

The source change, and the measured result (the app's judge): from 160, `if (c) { mode = 0x3E; } else { mode = 0x3D;
... }` written `mode = 0x3E; if (!c) { mode = 0x3D; ... }`, the block's trailing `flags = 0;` at its top, and an empty
`do { } while (0);` before the enclosing statement: 0. Without the fence 723, and the pointer is folded to the symbol
(`lhu $v0, %lo(D - 4)`); the fence is a no-op the match relies on. Family: `default_before_test`. The bit clear needed a
`u8 *r = &G; *r &= ~0x10;` so the address is in `$v0` (210 -> 160, found by the library's `arm_private_rename`).

Failed experiments: `flags = 0;` at the top alone 360, the inverted test alone 883.

That cse stops folding at the empty loop's notes is a hypothesis from the output; one function, exact.
