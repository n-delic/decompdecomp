---
title: Resizing the stack buffer a call fills to the room the target's frame leaves
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: hypothesis
date: 2026-11-27
---

What the function looked like and the exact compiler and flags:
An open 60-instruction N64 function (best 121) whose frame is 0x40 with a 0x20 buffer. Compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2.

The source change, and the measured result:
HYPOTHESIS ONLY. Family `stack_buffer_frame_fit` sizes the single `spNN` array passed to a call to the room between the outgoing-argument area and the lowest saved register in the asm. There is no matched function behind it; on that function an earlier attempt at the larger buffer scored 128 against 121 for the smaller, so it may not help there. Treat it as something to try, not a rule.

Failed experiments:
None recorded beyond the limits stated above.

Evidence level: hypothesis.
