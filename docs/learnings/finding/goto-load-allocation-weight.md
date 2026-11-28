---
title: goto load allocation weight
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

In a 583-instruction N64 state handler, a real post-call indexed load sits in a backward-goto loop. Wrapping that assignment in four nested `do { ... } while (0)` scopes improved the linked judge's O1 diagnostic from 7,215 to 7,120. The input moved to the target's saved register, removing 19 mismatched register rows. Compiler allocation dumps support the explanation that loop-depth weighting changes local register priority. The wrappers are scaffolding, not evidence of the original source.

The generated child scored 23,249 using the function's saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
