---
title: Target-evidenced switch upper guard
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler has a nested switch on an integer byte value
with cases 7, 10 and 16 and no default. The target compares the selector to
20 immediately before its switch dispatch. Adding `if (selector < 20)` around
the switch is behavior-preserving for these case values and generated the
observed guard under gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`.

The isolated judge moved from 4125 to 3925, and the app recorded 3925. A
`target_switch_upper_guard` child reproduced the same score. One earlier
source moved from 6245 to 6050 with the same guard; converting the other
control cases into a guarded switch scored 6445, and combining both edits
scored 6250 on that earlier source.

These are observations on one function, not controlled exact-match tests.
The repair tests guard against changing behavior when a case lies beyond the
bound or a default, volatile type, goto or unsafe conditional is present.
