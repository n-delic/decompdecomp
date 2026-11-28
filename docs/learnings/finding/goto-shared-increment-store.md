---
title: goto shared increment store
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler computes an increment into a local and jumps to a shared pointer store immediately followed by another label. Copying that store into one predecessor and jumping to the second label improved the linked O1 diagnostic from 3,260 to 3,245. The temporary has no other reads. After other changes, staging the first slot pointer and using the shared store improved 510 to 445; its O2 score was 20,643. Control-flow liveness confirmed the temporary was dead after the store.

The generated child scored 21,533 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
