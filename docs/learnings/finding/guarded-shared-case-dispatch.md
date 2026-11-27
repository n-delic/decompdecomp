---
title: guarded shared case dispatch
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction N64 MIDI handler built with GCC 2.7.2 KMC and `-G0 -mips3 -mgp32 -mfp32 -O2` had a switch whose draft omitted a direct command case. The target compares the missing value and enters the same call setup reached by a zero guard in another command case. The guard's entry starts one instruction earlier to set an argument, while the direct dispatch sets that argument in its branch delay slot.

A target-evidenced rewrite moved the guarded else arm behind a missing case label and kept a break in the nonzero arm. The real judge improved from 7,560 to 6,870. This corrects omitted target behavior; it is not an equivalent rewrite of the incomplete draft. The assembly proof checks both selector comparisons, the zero guard, adjacent shared entries and a call; the case must be absent in the same switch.

This is one observed function, not a match or a controlled compiler regression. Synthetic tests exercise the guard, shared call, missing case, and rejection of ambiguous dispatch.
