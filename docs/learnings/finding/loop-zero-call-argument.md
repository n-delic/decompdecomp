---
title: Loop index as zero-valued call argument carrier
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

One MIPS state machine had two remaining register differences when staging a constant outgoing call argument. Replacing that constant in both shared call sites with `constant | (i & ~i)`, using the initialized enclosing loop index, changed the staging register and took the linked app judge from 10 to 0 across 496 instructions. The bitwise term is always zero; it and the two one-shot scopes around a labeled store are allocation scaffolding and must be disclosed. The O1 profile was applied to this function only.

The register and score change is an observation for this function; the precise compiler-pass cause was not established. The generated repair child independently scored 0. It requires an unescaped initialized loop index, rejects writes and incoming jumps that could bypass initialization, and proposes the same constant-position change at both call sites. Synthetic tests cover these guards, not additional game matches. Casts of the literal alone stayed at 10; using a constant local immediately before the call scored 370.

The target caller uses the return register of a helper whose saved matched C was declared `void`. A locally tested correction changing that helper to return its computed value also scored 0, but the app's readable-code tool required a function rename before saving it. The cross-function C contract therefore remains a separate quality issue.
