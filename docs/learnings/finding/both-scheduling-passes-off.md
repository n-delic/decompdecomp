---
title: Both scheduling passes off together, neither alone
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 103-instruction N64 function of sinf/cosf
products stored into a record array (gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`). The saved C reached 180 with
`do { } while (0)` fences. In the target the index load and the address `lui` came first, in source order.

The source change, and the measured result before and after (scores, the judge used): plain C (a record array, the first
`sinf(a) * 1050.0f` staged in a local, `f32 *ang = &angle;`) scored 1275 at `-O2`, 1285 with `-fno-schedule-insns2`,
1060 with `-fno-schedule-insns`, and 0 with both flags (the app's own judge, one function).

Failed experiments that would otherwise be repeated: reordering statements and fences at `-O2`; `-O1` (1317).

What is controlled evidence and what is one function: one function. `scheduler_fingerprints.variants` now also tries
both passes off for an `-O2` profile with no scheduling flag; it still needs a candidate whose diff is order and
register rows.
