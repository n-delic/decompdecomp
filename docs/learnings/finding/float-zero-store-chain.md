---
title: float zero store chain
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2 and -O1
evidence: observation
date: 2026-11-27
---

An N64 function compiled by GCC 2.7.2 KMC with `-G0 -mips3 -mgp32 -mfp32 -O2` stores float zero to three consecutive fields. The target loads zero into a float register and uses it for all three `swc1` stores; independent literal assignments in the draft used integer zero stores instead.

Chaining the three assignments in their original store order produced the float stores and improved the app judge score from 48,075 to 47,535. The same change with `-O1` improved the diagnostic score from 3,410 to 2,610. Staging zero in an existing float local also helped, to 47,545 and 2,630 respectively. A repair checks for the target float-zero load and the same three field offsets before offering the chain.

This is one observed function, not a match or controlled compiler regression; synthetic tests cover the source transformation and evidence guards.
