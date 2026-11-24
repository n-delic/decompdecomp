---
title: copy load read early after an earlier store
kind: finding
compiler: sn-n64-281 (SN gcc 2.7.2), -G0 -mips2 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: three small N64 functions (31, 46 and about
40 instructions; SN gcc 2.7.2, `sn-n64-281`, `-G0 -mips2 -O2`), each a run of plain stores whose last statement was a
float or word copy (`dst[0xC] = src[0xC]`) or an update (`a[0x80] = a[0x80] + 0xA`). The target does the load earlier:
right after a store four statements above, between two stores, or right after a call. Everything else matched; the diff
was two reordered rows (60).

The source change, and the measured result before and after (scores, the judge used): read the load into a new local
right after that earlier store (or call) and store the local where the statement stood (`t = src[0xC]; ... dst[0xC] = t;`),
for an update `t = a[0x80]; ...; do { a[0x80] = t + 0xA; } while (0);` (the fence kept; dropped, 430; added to an unfenced
write was the other form). Or, where the copy simply follows an independent store, swap the two statements. 60 -> 0 on all
three with the exact judge (fast_judge, then `prove_chain --families all` on the pinned library). Hypothesis, not read in
the compiler source: sched keeps a load behind the stores written before it unless it is written above them.

Failed experiments that would otherwise be repeated: `temporary_hoist` (assignment moved up as far as `_earliest` lets
it) never offered it, because that stops at the first store through another pointer; 138 to 200 children of every other
family scored none under 60. Moving the read above a call changed the value (365).

What is controlled evidence and what is one function: three functions, one shape each, all judged exact; the family
`copy_load_hoist` (decomp-repair 0.5.1015) offers both forms. Over the 1053 progress sources of one game it fires on 56
(249 children, at most 24 per function) before judging.
