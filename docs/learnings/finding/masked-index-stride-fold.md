---
title: masked index stride fold
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler has two expanded calculations of 100 times a masked 16-bit parameter, with the first multiplication staged in separate locals. Inlining those products or folding each complete bounded expression to a multiply by 100 improved the linked O1 diagnostic from 3,245 to 2,210. Folding only the first or second scored 2,540 and 2,485 respectively.

The generated combined child scored 21,533 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
