---
title: stored pair slide with member readback
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 33-instruction effect update in an N64 game bumps a record's counter, advances a phase, writes three shade bytes
and a halfword height, then compares the height. SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp64`. The kept best scored 180; the app had 23 earlier attempts and 3006 automatic repair trials on it.

The source change, judged with the app's judge, starting from the `loop_fence_unwrap` child (120):

| edit | score |
| --- | --- |
| compute and store the height above the phase update (the two statements together) | 300 |
| compare reads the stored halfword back instead of `(height & 0xFFFF)` | 955 |
| open the outer `do { } while (0)` | 180 |
| move + open | 45 |
| move + read-back | 120 |
| move + read-back + open | 0 |

So the height was computed before the phase (its `lhu` sits next to the counter's load in the target) and the test read
the member. decomp-repair `stored_pair_slide` offers the three as one child; from the kept best `prove_chain.py` reaches
0 in two steps (`loop_fence_unwrap`, then `stored_pair_slide_readback`). `stored_member_readback` had offered nothing
because it did not read the printed spelling `*((s16 *) (p + K))`; that is fixed in the same release.

A typed rewrite also matched (counter `++`, height from `(u16) base`, then phase, then the shade bytes as one chained
assignment `b = g = r = value`, whose stores come out `r`, `g`, `b`). Written `r = g = b = value` the stores are
reversed (20); one assignment per field scored 2015 with the height last.

This is one function; the three-edit table is a measurement on it, not a controlled test.
