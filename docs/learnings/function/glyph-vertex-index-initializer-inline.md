---
title: a one-read index local in a declaration swaps two address loads (glyph model drawer, 20 -> 0)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-28
---

A 143-instruction function that draws one vector glyph: transforms the model's points, writes them as screen vertices
at an offset, then emits F3DEX2 vertex and line commands. It started from an m2c draft at 3088 after 99 attempts.

Source changes and the app judge's scores:

1. Adapted the C of a matched sibling that draws a whole model (same structure, the same globals): 505.
2. The vertex command word with the opcode constant first, `0x01000000 | ((n & 0xFF) << 12) | ((n & 0x7F) << 1)`
   instead of `a | (b | 0x01000000)`: 505 -> 20 (the library's `or_constant_lead` and `or_chain_order` do this).
3. `{ s32 base = gCount; vtx = &((Vtx *) gDots)[base]; }` written as `vtx = (Vtx *) gDots + gCount;` or
   `&((Vtx *) gDots)[gCount]`: 20 -> 0. The only difference at 20 was the two `lui` of the two globals in each other's
   places: with the index in its own declaration its address is formed first.

Step 3 had no library family: `single_use_inline` takes assignments and literal initialisers, and other initialisers
only when read twice. decomp-repair `initializer_single_use_inline` (decomp-repair #2239) writes it. `prove_chain.py`
with `or_constant_lead,initializer_single_use_inline` goes 505 -> 20 -> 0 from the step 1 source.

Not reached automatically: step 1. Taking a sibling's body for a function that is not its twin (184 against 143
instructions) is still by hand.

The first version of the family refused any `volatile` in the project header and so offered nothing under the app,
while it worked on the bare function: test a new family through `prove_chain.py`, which passes the project header.
