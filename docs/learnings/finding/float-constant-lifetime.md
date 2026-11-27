---
title: float constant local lifetime
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A MIPS function uses a float for a pitch adjustment and later uses a second float for an unrelated pan adjustment. The target saves both float registers. A faithful reconstruction kept the two lifetimes separate, and the compiler reused one saved register for both.

Assigning the `10.0f` pitch adjustment to the later pan local before subtracting it from the pitch changed the isolated exact-judge score from 272 to 10. Both programs preserve the same literal and arithmetic, and the pan local is overwritten before its next use. Two remaining target rows load the adjustment in a temporary floating-point register while the candidate loads it in the saved register. The reason GCC selects these registers is a hypothesis.

Using the later local for the product in both pitch branches scored 160; enclosing the initial pitch assignment in a one-shot scope with that product reuse scored 45. Holding the 50.0f divisor in the later local scored 420 or 525. These are observations on one function, not a controlled compiler regression. The target profile is supported by nearby matched functions but was only trialled, not applied, as the candidate is not exact.
