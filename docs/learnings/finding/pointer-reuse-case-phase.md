---
title: Reuse a pointer across switch case phases
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler used a search-created pointer local for one
switch case and another pointer local for disjoint case phases. Reusing the
latter local for both, with the former declaration removed, changed the
isolated judge from 1700 to 1665 under gcc-2.7.2-kmc
`-G0 -mips3 -mgp32 -mfp32 -O2`.

The existing CFG-based `pointer_local_reuse` repair knew how to prove the
lifetimes disjoint, but its three-pair limit and one-way declaration choice
excluded this child; its local lane did not call that repair. The library now
prioritizes phase names, offers both declaration directions, and routes the
existing analysis to the local lane. The app was offline, so 1665 remains an
isolated result. This is one function observation, not a controlled exact
match; the allocation explanation is a hypothesis.
