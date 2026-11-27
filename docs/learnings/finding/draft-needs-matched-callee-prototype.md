---
title: A machine draft needs its matched callees' prototypes
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: spawn routines of 556 to 896 instructions that
call one matched 18-parameter function (six byte/word parameters, three pointers, nine trailing f32s) dozens of times.
Their saved -O2 bests scored 28509 to 51657; KMC gcc 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O1` is their level.

The source change, and the measured result before and after (scores, the judge used): the shared header did not declare
the callee, so m2c guessed its parameters (floats as integers, bytes as words). On an 896-instruction one, judged by the
app's judge-worker: the draft without the prototype 25161; with the matched definition's prototype in the m2c context and
in the draft's source 635 after naming the data symbol's type by hand, 0 after writing its three address temporaries
out. From the app's own draft pipeline: 3325, then `unknown_symbol_offsets` (byte offsets for `&D + K` passed on) and
three `single_use_inline` steps reach 0. The app now adds those prototypes itself (`app/callee_prototypes.py`).

Failed experiments that would otherwise be repeated: giving m2c the prototype without declaring it in the draft: the
compiler sees an undeclared callee and promotes every float to double (73335).

What is controlled evidence and what is one function: the before/after scores are one function. Hand-written C with the
true prototype was exact first or nearly first on three more spawn routines of the same shape.
