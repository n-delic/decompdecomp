---
title: goto shared tail copy
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2 and -O1
evidence: observation
date: 2026-11-27
---

In an N64 function compiled by GCC 2.7.2 KMC at `-G0 -mips3 -mgp32 -mfp32 -O2`, two distant paths jumped into a shared byte-zero store in the final arm of an earlier conditional. After that store, control fell through to the common continuation.

Copying the byte store into the later guarded path and jumping to the same continuation improved the app judge score from 46,980 to 46,375. The `-O1` diagnostic improved from 2,285 to 1,800. Copying both later jumps together instead gave 46,990 and 2,150; copying only the other jump gave those same worse scores. This is one function observation, not a match or a controlled regression test. The copy must keep the original continuation and the other incoming jump intact.
