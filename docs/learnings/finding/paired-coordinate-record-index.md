---
title: paired coordinate record index
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS rendering function passes two coordinates and a third table entry
indexed by the same record's byte field. The app's O1 diagnostic was 550.
The ordinary paired-coordinate rewrite alone raised the linked score to 1230.

As one combined change, stage the record's actual masked index and byte offset,
then stage both coordinate addresses with the second address assigned first.
That changed the app's diagnostic from 550 to 410. Both addresses and both
record-index locals feed the original reads; the one-shot scopes are
compiler-matching scaffolding. This is one observed function, not an exact
match or a controlled compiler rule.

The paired-address shape with an unscoped record-offset assignment scored 460;
the same combined shape without swapping the coordinate assignments scored
495. Scoping the masked-index assignment instead regressed past 600. These
numbers came from a read-only linked comparison that reproduced the app's
550 and 410 results.
