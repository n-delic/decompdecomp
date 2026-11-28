---
title: Hold a field across consecutive checks
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

One MIPS state machine tested a nonvolatile record field in an `if` and immediately tested the same field again in its `else if`. Reading the field into an integer local immediately before the first test and using that local in both conditions removed four extra instructions and improved the linked diagnostic judge score from 415 to 10. The second condition runs only when the first arm is not taken, so there is no intervening body that can modify the field. The function remained open and its O1 compiler profile was diagnostic only.

The target used one loaded register for both comparisons. The precise compiler-pass cause of the repeated candidate load has not been established. A declared repair emits the measured child only for a visible nonvolatile record field and rejects macro-sensitive names or placement under an unbraced conditional. Its generated child independently scored 10 in the judge; synthetic tests cover the safety guards, not other game matches. The candidate retained separately disclosed one-shot allocation scopes.
