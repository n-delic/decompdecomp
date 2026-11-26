---
title: integer argument early local
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-26
---

A small N64 object constructor (void, one pointer parameter) that ends with an eleven-argument call: the
last argument a literal stack word, the call result stored through the object it allocated.

Source change and score (the app's standalone judge): the saved best passed `0x46` inline, 225. Declaring `s32 k46;`
and setting `k46 = 0x46;` BEFORE the earlier statement `temp_s2 = arg0 + 0xD4;`, with the call's stack argument written
`k46`, scored 0. The target loads the constant into a register ahead of that unrelated sum; setting it on the line
above the call (what `constant_in_local_argument` does, for floats) scored 225 here, so the place is the evidence.

Now decomp-repair `integer_argument_early_local`: a literal other than 0 and 1 passed to a call, stack arguments first,
set before the call's statement and before each of up to three plain statements above it in the same block; with the
target's assembly the literal must be loaded by an immediate. `prove_chain.py --families all` from the saved best reaches 0
in one step (28 children for this function; the `0x46` set before the sum is the exact one).

Failed experiments: the same set directly above the call (225); the same for the other stack literals (`0xFF`, `0xE3`,
`0x4F`) in each place (20 to 537, none exact).

Controlled: the pre-match source and the hand match differ by exactly this edit, and the family reproduces it. One
function, one literal.
