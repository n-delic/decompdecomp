---
title: merge disjoint temporaries
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: a small N64 function that calls a routine
three times, each result used once by the next call. m2c names the one saved register's three values `temp_s0`,
`temp_s0_2`, `temp_s0_3` and wraps each statement in `do { ... } while (0);`. SN GCC 2.8.1 (sn-n64-281), `-G0 -mips3 -O2
-mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

The source change, and the measured result before and after (scores, the judge used): one variable declared once and set
three times, with the fences gone: 120 -> 0 on the hand-matched function. Hypothesis (not read in the compiler's source):
the first scheduler moves a value that is set once down to just before its first use; a variable set several times has no
such move-down, so the arguments of the calls come out in source order, as the target has them. Library family
`merge_disjoint_temporaries` (decomp-repair, whole run and each neighbouring pair, with and without the fences; gate:
same base name and type, disjoint live ranges, no loop or backward goto holding two of them).

Failed experiments that would otherwise be repeated: on a small synthetic three-call function the fences alone changed the
schedule (39 lines of diff output on a 34-line listing) and the merge on top changed nothing, so the merge is not
the lever by itself in every shape; the fences are. One step of the family on 16 small saved bests of the same project
(no claims, read only) improved none of them.

What is controlled evidence and what is one function: the 120 -> 0 is one function; the synthetic compile is a
controlled comparison of fenced and unfenced spellings only. The family fires on 227 of 837 saved bests, so a firing is
not evidence of the shape.
