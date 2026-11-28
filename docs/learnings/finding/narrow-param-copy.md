---
title: narrow parameter copy
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler copies a word parameter to an unsigned halfword local before a table access, with a second identical assignment on one path. Replacing the copy's use with the same halfword conversion of the unchanged parameter improved the linked O1 diagnostic from 3,605 to 3,530.

The generated child scored 21,943 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
