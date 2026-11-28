---
title: narrow index product stage
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

In a 583-instruction N64 state handler, the draft calculates a byte index times `0x178` inside a nested call after calculating two other table offsets. Staging that real product in an integer local immediately after the index load improved the linked O1 diagnostic from 6,850 to 6,325. The narrower source local and bounded constant make this multiplication fit in `int`.

The generated child scored 22,954 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
