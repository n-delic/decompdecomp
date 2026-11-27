---
title: A pitch value held in one float: merge the consumed value and the call result
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-27
---

A 69-instruction N64 voice pitch update: it reads a glided pitch, adds the bend and an offset, and when the sum
changed passes it through an exponential and clamps the result. KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2` (the
profile of its matched neighbours; the project had it under an SN GCC 2.8.1 `-mfp64` profile, where the same C
scored 1575). The target uses even float registers only.

Source change: the draft's three float names (the value read, `sum = value + (bend + offset)`, and the call's result)
written as one variable. Under the KMC profile the three-name form had only register rows (15 of them, score 75, the
app's judge-worker); one variable was exact (0).

Failed experiments (all stayed at 75): merging only the read value with the sum, merging only the sum with the call
result. An earlier session also tried a separate glide local, a block-scoped base, reordered declarations, staging
the bend sum and splitting the loaded value from the sum: all 75.

`float_local_merge` (decomp-repair) offered each disjoint pair but refused a pair where the second value's first
assignment reads the first, and never wrote three names as one; the best step it had was 55. It now emits the chain as
one child, which scores 0 from the 75 draft. Controlled on this one function; that one pseudo keeps the target's
register is a hypothesis, not checked in the compiler's source.
