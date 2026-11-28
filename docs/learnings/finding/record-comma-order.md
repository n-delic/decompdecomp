---
title: Local record field order in a comma guard
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio dispatcher initializes a local voice configuration in a short-circuit comma expression before passing its address to a helper. Its three fields have independent storage; the record has not escaped when these writes run.

Swapping the zero initialization of the last field with the preceding priority-field load improved the ROM judge's score from 2049 to 1989. The same improvement occurred after spelling the guard as early exits, a tied preliminary rewrite. The `record_comma_order` repair emits the measured swap in both the draft and local lanes and rejects prior escape, unions and overlapping fields. This is one near-match observation, not a controlled exact match.
