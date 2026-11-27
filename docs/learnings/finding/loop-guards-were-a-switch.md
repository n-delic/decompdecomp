---
title: Loop-tail if/continue guards with a goto to the loop head were a switch
kind: finding
compiler: SN64 GCC 2.8.1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 56-instruction N64 message thread,
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Its machine draft read a two-case dispatch at the end of an
endless loop as `if (kind == 1) {...; continue;}`, a product staged for the second arm, `do { if (kind != 2) goto head; }
while (0);` and the second arm's statements. The target tests both values in one chain ahead of both arms (`beq`, `beq`,
`j` back to the loop head) and keeps `2` in a saved register differently.

The source change, and the measured result before and after (scores, the judge used): writing the guards as
`switch (kind) { case 1: ... break; case 2: ... break; }`, arm bodies verbatim, took the saved best from 1126 to 0
(decompdecomp's exact judge). decomprepair `exit_dispatch_switch` emits it and reached 0 from the saved best, its
scaffolding-stripped form and its clean rewrite.

Failed experiments that would otherwise be repeated: none; no installed switch family offered a child on this shape.

What is controlled evidence and what is one function: one function. The reason (a `switch` is expanded as one compare
chain, `expand_end_case`) is a hypothesis, not read from the compiler's source.
