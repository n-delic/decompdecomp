---
title: far fields m2c names as globals are members of one struct at the base the target builds
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 67-instruction N64 boot loader (message queue, ROM-to-RAM DMA, then four stores into two large frame records).
Saved m2c best 2270 -> 0 with three one-step edits, each an improvement on its own from the state before it
(the app's exact judge):

1. The four stores went to globals named by their own addresses (`D_X = 2;`) that the target never names: it builds the
   address of a lower symbol (`lui; addiu %lo`) and adds the rest. Written as members of one struct at that symbol:
   2270 -> 1185. decomp-repair `far_global_member`.
2. m2c's three stray arguments on a one-argument SDK call dropped: 1185 -> 40. `callee_arity_trim`.
3. `start = ROM_START; size = ROM_END - start;` and `start` passed to the DMA (the target loads the start address before
   the end address): 40 -> 0. `symbol_address_local`.

A typed rewrite from the assembly reached 0 the same way; two same-sized stack buffers had to be declared in the
target's frame order (the m2c draft already had it).

Controlled, with the other two edits held: separate globals 2120, `*(T *)(base + off)` casts 2230 (GCC folds the
constant back into the symbol), a struct declared at the base 0 (a flat struct and an array of two records both match).
Only this function; why GCC keeps the base in a register for a member is a hypothesis (the offset exceeds a 16-bit
displacement and the object is one symbol).
