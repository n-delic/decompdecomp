---
title: a pointer stored into another field and cleared through a second read of the first (`move $v1,$v0` before the store)
kind: function
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 78-instruction N64 effect update, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Its tail
parks a child object: `lw $v0,0x48($a1); move $v1,$v0; sw $v0,0x50($a1); sw $zero,0x4c($v1)`. The only difference left
was the missing `move` (draft's plain form 105, saved best 125 with a `do { } while (0)` around the first statement).

The source change, and the measured result before and after (scores, the judge used):
`t = obj->cur; obj->prev = t; t->link = 0;` became `obj->prev = obj->cur; t = obj->cur; t->link = 0;` (the app's
judge): plain form 105 -> 0, saved best 125 -> 20 (0 with the loop wrapper opened as well). decomp-repair
`published_field_reread`.

Failed experiments that would otherwise be repeated:
All stayed at 105 on a typed rewrite: `obj->prev->link = 0` after the copy, `n = obj->prev = obj->cur`,
`(obj->prev = obj->cur)->link`, reading the field just written back into the local, a pointer to the slot, `do { }
while (0)` around the load or the clear, static inline helpers for the store and the clear. Loading the local first and
then storing a second read (`n = obj->cur; obj->prev = obj->cur;`) gave 110. Reusing the same local for two earlier
blocks and then the re-read gave 10 (the copy's registers swapped).

What is controlled evidence and what is one function:
One function. That cse reuses the first load for the second read and the allocator then keeps the copy is a hypothesis
from the output; no pass dump was read.
