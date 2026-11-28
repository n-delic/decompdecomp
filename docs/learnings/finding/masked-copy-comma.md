---
title: masked copy in comma expression
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler copied an unchanged word parameter masked to 16 bits into several locals. The existing masked-copy repair improved its linked O1 diagnostic from 6,325 to 5,270. One copy also appeared in a comma expression; replacing that assignment expression with the same masked value and inlining the other reads improved 5,270 to 5,200. A second copy in a logical-AND expression improved 5,200 to 5,020 when inlined the same way; its generated child scored 22,853 at O2.

The generated child scored 23,404 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
