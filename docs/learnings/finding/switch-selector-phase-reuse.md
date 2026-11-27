---
title: Reuse a case-local word for switch dispatch
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler computed a masked command in one `s32` local,
then computed a separate selector in a switch case. Reusing the case-local
word for the earlier command changed the isolated judge from 350 to 295 with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The first value is dead after
dispatch, and every case use of the replacement follows an assignment.

The `switch_selector_phase_reuse` repair checks CFG liveness, initialization,
macro capture and case boundaries before proposing this combination. The app
was offline, so 295 is an isolated result. Reusing other word locals scored
490 or 1326. These are observations on one function, not controlled exact
match tests; allocation explains the changed registers only as a hypothesis.
