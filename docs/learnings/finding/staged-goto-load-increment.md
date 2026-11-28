---
title: staged goto load increment
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler loaded a value, copied the pointer, incremented the shared value and jumped to a shared store. Staging the actual load in a separate signed 32-bit local before copying the pointer and value reduced the linked O1 diagnostic from 170 to 150. A generated child reproduced the result from the preceding 175-point source.

The same change scored 24,833 under the saved `-O2` profile, worse than 20,118, so it was not submitted and the profile was not changed. This is one function observation, not an exact match or a controlled regression test.
