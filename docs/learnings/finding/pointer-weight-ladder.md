---
title: Pointer assignment allocation weight
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio dispatcher keeps an event pointer derived from its argument live across calls. One candidate put three nested one-shot scopes around the pointer assignment, and one around a following byte read; it scored 2094 in the ROM judge.

Adding four more one-shot scopes to the same assignment reduced the isolated judge score to 2049. Adding one, two or three more instead tied 2094. The `pointer_weight_ladder` repair offers those depths around ordinary pointer-offset assignments in both draft and local lanes. The scopes are compiler-allocation scaffolding, not recovered evidence of the original source. This is one observed near-match, not a controlled exact match.
