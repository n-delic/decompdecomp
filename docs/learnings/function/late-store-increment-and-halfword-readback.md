---
title: late store increment and halfword readback
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 31-instruction N64 effect update (a counter
bumped by one, a phase word advanced, three colour bytes from the phase, a halfword height from a base and the counter,
a limit test that releases the object). SN GCC 2.8.1, the flags above.

The source change, and the measured result before and after (scores, the judge used): from the m2c draft (990, the app's
judge-worker), `t = F + K; ... F = t;` written as `F += K;` where the sum was computed, for both sums, plus the stored
halfword compared as the member (`*(u16 *)` instead of `(height & 0xFFFF)`): 0, recorded by the app. The increments
alone were 1260 and the readback alone 1610. `field_increment_reread` only rewrote the sum whose store is adjacent.
The repair is `late_store_increment` (families `late_store_increment_reread`, `late_store_increment_readback`); its first
child on the draft is exact.

Failed experiments that would otherwise be repeated: a chained assignment for the three colour bytes changes nothing
(990 stays 990). `prove_chain.py` from the draft reached 340 (`parameter_read_scope`, `product_inline`) within 120 judged
variants, with scaffolding.

What is controlled evidence and what is one function: the four combinations of the two edits were judged on this one
function.
