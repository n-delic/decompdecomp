---
title: Stage target reads before a flag store
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler's draft stores a zero flag before reading two
words and storing their sum. The target loads both words, stores the flag,
then adds and stores the sum. Staging the sum before the flag store changed
an isolated gcc-2.7.2-kmc judge from 1910 to 1700 with
`-G0 -mips3 -mgp32 -mfp32 -O2`.

`target_sum_before_flag` emits the exact candidate only with evidence of the
loads, flag offset, sum and destination in that target order. This changes
behavior if the flag aliases a source word, so it corrects the draft using
target evidence rather than claiming unconditional source equivalence. The
app was offline; 1700 is an isolated result and has not been submitted.
Writing the sum store before the flag, or staging the two reads separately,
also scored 1700. Keeping the flag store first scored 1910. These results
are observations on one function, not controlled exact-match tests.
