---
title: A stack array passed to a callee is as long as the callee writes, not as the caller reads
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: verified
date: 2026-11-27
---

What the function looked like and the exact compiler and flags: a 66-instruction N64 function (KMC GCC 2.7.2
`-G0 -mips3 -mgp32 -mfp32 -O1 -g`) that fills a three-halfword vector, hands it and an output vector to a transform
(`f(in, out, &extra)`) and reads back `out[0]` and `out[1]`. The callee's matched C writes `out[0..2]`. m2c declared
`s32 sp18[2];`, so the frame was 0x30 against the target's 0x38 and `&extra` sat at `0x20($sp)` instead of `0x28`.

The source change, and the measured result: `s32 sp18[3];` (the callee's three words), 7 -> 0 in the app's judge.
`sp18[4]` (the full room up to the next addressed object) is also exact.

Failed experiments that would otherwise be repeated: an unused padding array also sized the frame
(`frame_gap_scaffold` reaches 0), but it is scaffolding the game did not have; earlier agents had it refused.
`stack_object_size` and `stack_object_extent` only grow scalars m2c declared, not an array declared too short.

What is controlled evidence and what is one function: one function. decomp-repair `stack_array_extent` (0.6.293) grows
a stack array passed to a call up to the next stack offset the target addresses, smallest count first.
