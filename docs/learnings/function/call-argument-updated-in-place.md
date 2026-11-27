---
title: call argument updated in place
kind: function
compiler: sn-n64-281 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 155-instruction N64 asset loader that fills several buffer pointers, then passes one of them through an alignment
macro to the last call that reads it. SN `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.
The saved best was 380 with every remaining row a rotation of three saved registers.

The source change, and the measured result before and after (scores, the judge used):
App judge. Moving the buffer-pointer assignments out of the declarations and into the target's order: 380 -> 80.
`size >>= 4; size <<= 4;` for `size = (size >> 4) << 4;`: 80 -> 70. `limit = ALIGN16(limit); f(dst, limit);` for
`f(dst, ALIGN16(limit));`: 70 -> 0. decomprepair `call_argument_inplace_update` writes the last step.

Failed experiments that would otherwise be repeated:
`do { } while (0)` around the last call, the copy, or the whole guarded block (130 to 985); dropping either pointer
copy (415 to 805). `prove_chain` found no improving child among the first 90 from the 380 best.

What is controlled evidence and what is one function:
One function. That the extra reference to the local decides its saved register is a hypothesis.
