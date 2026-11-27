---
title: float shared store branch
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2 and -O1
evidence: observation
date: 2026-11-27
---

An N64 function built by GCC 2.7.2 KMC with `-G0 -mips3 -mgp32 -mfp32 -O2` tests a float against zero and a positive threshold, then stores a byte zero in either of two branches. The target uses a branch-likely into the shared store while the draft had a redundant branch and duplicated the store.

Changing the positive branch to jump to the identical store in the negative branch improved the app judge score from 47,535 to 47,035. The same source with `-O1` improved the diagnostic score from 2,610 to 2,530. A combined boolean expression worsened the diagnostic score to 3,345; merely simplifying the inverted nested test gave 2,610.

This is a single function observation, not a match or a controlled compiler regression test. The change preserves the original comparison structure, including its behavior for unordered floats.
