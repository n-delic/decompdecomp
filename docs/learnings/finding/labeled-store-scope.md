---
title: Labeled constant store allocation scope
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

In one MIPS state machine, the target held a status constant and a table address in the opposite saved registers from the candidate. Enclosing the existing constant array store immediately after a label in two one-shot `do { ... } while (0)` scopes reduced the linked judge score from 1370 to 1195; this function was not yet matched. The label remained outside both scopes, so incoming jumps still execute the store. The scopes are allocation scaffolding and must be disclosed if used.

The compiler's `flow.c` counts references by loop depth, and `global.c` uses reference counts and live length in register priority. The allocation dump and resulting score support a register-weight explanation for this one function, not a universal outcome. A repair offers the labeled store at bounded depths, rejects macro-sensitive names, and its generated child independently reproduced the 1195 score in the exact judge. Synthetic tests cover routing and label placement; they are not additional game matches.

A single scope and other scoped local initializations did not beat the best measured score. The candidate was tested under the stated O1 profile as a diagnostic trial; no compiler setting was applied to this still-open function.
