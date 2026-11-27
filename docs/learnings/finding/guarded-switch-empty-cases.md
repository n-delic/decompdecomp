---
title: Guarded selector and no-op switch cases
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler's draft used a guarded low-value switch and
two high-value comparisons. The target's decision tree compares the selector
against 20, 28 and 92. A public SDK implementation has a single control
switch containing a group of disabled controller cases, suggesting no-op case
labels as a source shape.

Replacing the compare tree with a single switch and adding no-op cases 20
through 27 and 93 changed the isolated score from 1665 to 675 under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. Omitting case 93 scored 975.
Both source forms leave these values as no-ops. The
`target_guarded_switch_cases` repair emits both candidates when the target
has the range comparisons. The app was offline, so these are isolated results.

This is an observation on one function, not a controlled exact-match test.
The target comparisons support the search but do not prove the original labels
or the compiler's decision-tree mechanism.
