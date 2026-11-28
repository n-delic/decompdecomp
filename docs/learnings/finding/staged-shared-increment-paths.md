---
title: staged shared increment paths
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler had two increment paths joining a shared store. Staging the first path's table base, and staging the second path's pointer and increment value before copying them to the shared locals, reduced its linked O1 diagnostic from 250 to 235. Both paths retain their original loads, additions and store. The generated combined child reproduced the same score.

The combined source scored 20,988 under the saved `-O2` profile, worse than 20,118, so it was not submitted and the profile was not changed. Staging only the first path's base instead scored 510 under O1: GCC merged the increment paths. This is one function observation, not an exact match or a controlled regression test.
