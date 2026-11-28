---
title: Flatten an inverted nested if ladder
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

In one MIPS state machine, an error check was written `if (x != 5) { if (x != 0) A; else B; } else C;`. Writing the same paths as `if (x == 5) C; else if (x != 0) A; else B;` reduced the linked diagnostic judge score from 550 to 415. The branch bodies and when each condition executes stayed the same. This function was still open and the O1 profile was not applied.

The target placed the equal-case store before the unequal paths, but the precise compiler-pass cause was not established. The same rewrite earlier in a different control-flow context scored worse (1370 to 1670). A declared repair emitted the measured child and independently scored 415; its synthetic tests cover the preserved arms and reject unsafe surrounding shapes. This is one function's evidence, not additional game matches. The candidate also retained separately disclosed one-shot allocation scopes.
