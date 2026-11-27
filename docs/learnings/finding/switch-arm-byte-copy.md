---
title: Switch arm byte copy
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS event handler reads one unsigned byte and uses it in
several cases. Its first switch arm uses that byte repeatedly across calls.
The target includes a saved-register copy near the switch dispatch.

With gcc-2.7.2-kmc, `-G0 -mips3 -mgp32 -mfp32 -O2`, copying the byte to
another `u8` local and using that copy throughout the first case changed the
isolated judge score from 6555 to 6245; the app judge recorded 6245 too. The
`switch_value_copy` repair produces a child with the same 6245 score from the
6555 source. The relationship to register allocation is a hypothesis, not a
compiler-source finding.

Giving only a second byte the same treatment did not improve the score (6555).
Both edits together scored 6245. Opening the existing zero-trip fences before
this experiment improved the saved source from 6660 to 6555; the existing
`clean_rewrite` family already offers that form.

These scores are observations on one function, not controlled regression
results. The repair tests exercise eligibility and behavior-preserving guards,
not an exact match for this function.
