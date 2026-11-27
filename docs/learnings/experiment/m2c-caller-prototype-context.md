---
title: Giving m2c the matched callers' prototype of a many-argument callee made its drafts worse
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: N64 functions that call one 18-argument spawn
routine (four registers, two words, three pointers, nine floats) many times. The routine is not matched and the shared
header does not declare it; matched callers do. Judged with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`.

The source change, and the measured result before and after (scores, the judge used): m2c's own draft (no context,
`strict=False`) already recovers the 17 or 18 arguments at every call whose arguments are set in one block. Adding the
callers' prototype to the context did not help:

| function | draft, no context | draft with the prototype |
|---|---|---|
| 965 instructions, 32 calls | 38907 | 88852 |
| 612 instructions, 15 calls | 18860 | no draft |
| 919 instructions, 13 calls | 36234 | no draft |

Where there was no draft, m2c could not fill a stack argument of a call whose stores sit in an earlier block (a tail
the compiler merged); with the prototype that is an error, without it the call is only shorter.

Failed experiments that would otherwise be repeated: a drafting step that adds matched callers' prototypes to m2c's
context was written and not kept. What did match these functions was typed C with every call written out under `-O1`
(612 and 606 instructions exact on the first or fourth source); the app's `unscheduled_hint` did not fire for them,
since they copy fewer than two parameters in the prologue.

What is controlled evidence and what is one function: the table is three functions, one draft each.
