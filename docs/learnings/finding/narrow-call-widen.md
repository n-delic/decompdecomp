---
title: Explicit narrow conversion into a wide local
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio dispatcher assigns a call result to a signed 16-bit local and passes that local to another call. The local's address is not taken and its only assignment is the call result.

Changing `s16 x; x = call(...);` to `s32 x; x = (s16) call(...);` preserves the promoted value at each read while affecting the compiler's scheduling and register allocation. The live ROM judge's isolated worker improved from 2481 to 2386. The `narrow_call_widen` repair emits the measured child in both draft and local lanes and rejects address escape and further writes. This is one near-match observation, not a controlled exact match.

Changing the corresponding local in a different branch produced 2761 instead of 2481; the useful spelling depends on the surrounding lifetime.
