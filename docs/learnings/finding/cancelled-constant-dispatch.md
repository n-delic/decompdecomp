---
title: cancelled constant dispatch
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

In a 534-instruction N64 handler compiled with GCC 2.7.2 KMC at `-G0 -mips3 -mgp32 -mfp32 -O2`, a saved search candidate assigned 224 to an integer local before the dispatch and later added `(local - 224)` to a masked byte. Removing that constant assignment, its declaration and the cancelling addition improved the app-compatible judge score from 6,870 to 6,660.

The ordinary masked-byte expression and its C integer promotions remain unchanged. A reusable repair requires a single unconditional top-level constant assignment, no other local uses, the same subtracted literal and a plain byte operand; synthetic tests cover those restrictions. This is one observed function, not a match or controlled compiler regression.
