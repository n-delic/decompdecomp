---
title: nested call row address
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler computes a byte-table row address inside a nested call. Staging the same row address in a pointer immediately before the call improved the linked O1 diagnostic from 3,480 to 3,260. The other argument expressions in this case have no side effects, and both table reads remain.

The generated child scored 21,483 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
