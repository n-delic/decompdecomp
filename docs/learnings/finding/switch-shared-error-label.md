---
title: Shared error label in a switch case
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

In one MIPS state machine, a non-switch branch and a switch case shared an error store and counter increment through a goto. Moving the labeled tail from the non-switch branch into the case, while retaining the non-switch branch's early return and jumping to the moved label otherwise, reduced the app's linked diagnostic judge score from 1195 to 655. The tail kept two one-shot scopes around its store as allocation scaffolding. The function remained open and the O1 profile was not applied.

The target places the error block after the other case's body. This source arrangement changed the compiled branch and block layout; the exact compiler-pass cause was not established. Moving the label without retaining the early return scored 1765, and moving it after the switch scored 4350 in related variants. A declared repair emits the measured combined change while checking for intervening statements, multiple gotos and shadowing declarations. Its generated child independently scored 655 and synthetic tests cover its safety guards; this is evidence from one game function, not additional matches.
