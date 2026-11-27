---
title: Staging calls with a matched return width
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler passed two nested call results as arguments.
Its draft declared the first helper as returning `s16`, although the matched
callee returns `s32`. The target calls the first helper, keeps its full return
value across the second call, and narrows it to a halfword afterwards.

Correcting the prototype and staging both results in that target order
changed the isolated judge from 2745 to 2030 under gcc-2.7.2-kmc
`-G0 -mips3 -mgp32 -mfp32 -O2`. The `matched_callee_staged_args` repair
produces exactly that candidate when the matched callee and target sequence
provide the evidence. The app judge was still running when this was recorded.
Changing the prototype alone had scored the same as its original source in an
earlier probe; both changes together matter.

This is an observation on one function, not a controlled exact-match test.
The target sequence justifies the chosen call order; C leaves the order of
nested arguments unspecified, and side-effecting callees may distinguish it.
