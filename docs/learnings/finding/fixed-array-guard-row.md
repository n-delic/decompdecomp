---
title: Name a fixed-array row before a guard
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS drawing function used a pointer to fixed-width rows to check
`row[c][i]` against an end marker, then used `row[(u8)c][i]` in the copy.
The candidate scored 936 in the app's linked diagnostic.

Staging `line = row[c]` immediately before the guard and checking `line[i]`
reduced the linked score to 881. The later unsigned-index read remained
unchanged; it is not generally equivalent to the signed-index read. The app
recorded the new diagnostic under the same profile. A repair emits this
row-pointer form for simple guards.

Staging pointers for both accesses scored 1151. Different coordinate-pointer
spellings elsewhere in the function scored 936 or worse. The improvement is
one function's observed result; the register-allocation explanation is a
hypothesis rather than a controlled compiler-pass finding.
