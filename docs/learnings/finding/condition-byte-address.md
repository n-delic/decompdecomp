---
title: condition byte address
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler reads the same state byte twice in a condition with an intervening store. The target keeps the byte's address in a register and performs both reads through it; the draft materialized its address separately for each read. Staging its address in a local pointer immediately before the condition preserved both reads and improved the linked O1 diagnostic from 5,020 to 4,515.

The generated child scored 22,603 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
