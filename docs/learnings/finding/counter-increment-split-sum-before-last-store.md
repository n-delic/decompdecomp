---
title: a count advanced after stores into its own array element wants its sum taken before the last store and stored last
kind: finding
compiler: SN64 GCC 2.7.2 (sn-n64-281), -G0 -mips3 -O2
evidence: verified
date: 2026-11-24
---

What the function looked like: a 20-instruction N64 function that pushes a value onto a global array of 8-byte elements
kept at a global 32-bit count (`P = array + count * 8; *(s8 *)(P + 4) = 0; *(s32 *)(P + 0) = v; count += 1;`, after a
bounds test that calls an error routine). The saved best stood at 60 (two register rows: the second read of the count,
and the order of the word store).

The source change, and the measured result before and after (scores, the project judge): the count's sum in a local of
its own, taken between the byte store and the word store and stored after the word store:

    P = array + count * 8;  *(s8 *)(P + 4) = 0;  next = count + 1;  *(s32 *)(P + 0) = v;  count = next;

60 -> 0 (and 230 -> 0 from the m2c-shaped draft). The target reads the count a second time after the byte store and
before the word store, then stores the sum last; the compiler keeps no register for the sum across the stores.
Library: decomp-repair `counter_increment_split` (the sum before the last store), `_early` (before an earlier store) and
`_unwrapped` (the same with a `do { } while (0);` scaffold around the stores removed: the saved best had one). `counter_array_push`
(4-byte scale, one store, sum stored before the store) does not reach it. `prove_chain --families all` from the saved
best: 60 -> 0 in one step.

Failed experiments that would otherwise be repeated (each 60 or worse): `count = count + 1;` before the word store (120),
`count += 1;` last (60), the sum in a second temporary (230); inside the scaffold with the sum stored after its closing
(270). Reached 0 only through the scaffold `if (0) goto` and a record-layout rewrite as a combined step with
`--combine 10`, which is not a source anyone would keep.

What is controlled evidence and what is one function: one function, verified by the project judge, with single-edit
controls above. The family's reason (the sum's own load follows the byte store, which the compiler cannot prove disjoint
from the count) is a hypothesis. False-positive load: 2 of 2,022 matched/best sources of one 1,077-function project (a
sibling push that matched without it, and a different push in a loop); not judged.
