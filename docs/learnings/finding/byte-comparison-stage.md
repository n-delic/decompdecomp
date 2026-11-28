---
title: byte comparison stage
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler compares two independently indexed byte reads. The target loads the right operand before the left; the draft loads the left first. Staging the right byte read in a local before the comparison improved the linked O1 diagnostic from 4,015 to 3,605.

The generated child scored 22,148 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
