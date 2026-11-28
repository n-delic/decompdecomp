---
title: Weight a complete switch for allocation
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio dispatcher's typed SDK-shaped candidate scored 881 in the isolated ROM judge. Its incoming controller byte was allocated to a different saved register than the target.

Wrapping the complete inner controller switch in six one-shot scopes improved the score to 846; one through five scopes did not. The switch's own `break` statements retain their destination. The `switch_weight_ladder` repair offers bounded depths in both draft and local lanes, and rejects `continue`, jumps and opaque assembly. The scopes are allocation scaffolding, not evidence of the original source. This is one observed near-match, not a controlled exact match.
