---
title: float call literal order
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2 and -O1
evidence: observation
date: 2026-11-27
---

In one N64 function built with GCC 2.7.2 KMC and `-G0 -mips3 -mgp32 -mfp32 -O1`, an unescaped float constant local was assigned before a trigonometric call, while the target loads it after the call. Moving the assignment after the call improved the app diagnostic from 745 to 565; the `-O2` score remained 45,975.

In the same function, first assigning a float literal to the local used as the result of `(argument * literal) / divisor`, then using that local in the multiplication, improved the diagnostic from 565 to 545 and the app's `-O2` score from 45,975 to 45,965. Writing an equivalent scaled byte index as an unsigned left shift yielded 520 at `-O1`, without improving `-O2`. These are single-function observations, not a match or controlled compiler regression tests.
