---
title: Put a shared call in both conditional arms
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio dispatcher conditionally changed a pan local immediately before a shared voice-start call. The local remained available to later code; the source also had one-shot allocation scopes around the call.

Moving the call into both arms, while retaining the assignment in its original arm, improved the isolated ROM judge from 1211 to 881. The branch-local calls removed the one-shot scopes and GCC joined the call sequence again with a closer branch layout. The `conditional_call_branch` repair emits that child in both draft and local lanes, retaining the assignment and rejecting dangling control, opaque macros and non-integer conditions. This is one near-match observation, not an exact match.
