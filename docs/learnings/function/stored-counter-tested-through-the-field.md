---
title: stored counter tested through the field
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 23-instruction timer update of an N64 game: it bumps a record's word counter, stores a byte counter's old value
into two neighbouring bytes and increments it, then tests the word counter against a constant before two calls. The
target copies the stored word into a second register (`addu $a0,$v0,$zero`) ahead of the byte stores and compares the
copy (`slti $a0,$a0,0x3D`). Compiler and flags as above (the function's own profile).

The source change, and the measured result before and after (scores, the judge used):

The condition reads the member back (`if (obj->timer >= 0x3D)`) instead of the local that was stored. With the app's
isolated judge: the faithful m2c-shaped source (local sum, store, three byte stores, test of the local) scored 330 and
0 with only that change. The kept best (140), which stored a local only a dead `if (0)` block set, is 0 once that local
reads as the one it copied, the dead jumps go and the condition reads the field. As ordinary C the same function is
`obj->timer++; obj->a = obj->b = obj->c++; if (obj->timer >= 0x3D)`. decomp-repair `stored_compare_readback` and
`stored_compare_readback_dead_copy` do both.

Failed experiments that would otherwise be repeated:

`s32 t = ++obj->timer;` or a `t` local with `if (t >= 0x3D)` stays at 330 with either spelling of the byte stores,
the chained `a = b = c++` included. A chain over every library family from the m2c-shaped source, capped at 150
judged variants, stopped at 135 (`single_use_inline`). `stored_member_readback` only takes 8 and 16 bit members and
declines when anything but a local is written in between.

What is controlled evidence and what is one function:

One function. The reading that the member's value is a separate pseudo (a copy cse makes) which then gets its own
register is a hypothesis from the target's instructions; the compiler's source and dumps were not read.
