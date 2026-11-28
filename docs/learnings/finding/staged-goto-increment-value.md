---
title: staged goto increment value
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler staged an increment pointer, then computed the value and copied the pointer before jumping to a shared store. Adding a second signed 32-bit local to hold the computed value, and copying that local to the shared value after copying the pointer, reduced its linked O1 diagnostic score from 445 to 250. Both the pointer and value locals hold real expressions; the shared value is dead after the store.

The same change tied at 20,118 under the function's saved `-O2` profile, so it was not submitted and the compiler profile was not changed. Splitting the increment into a load followed by `+= 1` tied at 445 under O1. This is one function observation, not an exact match or a controlled regression test.
