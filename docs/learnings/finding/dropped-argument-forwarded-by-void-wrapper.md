---
title: A draft calls a pass-through wrapper with no argument, because the wrapper matched as (void)
kind: finding
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 38-instruction N64 asset-slot loader
(`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). It reads one record of an indexed table:
`lw $a0, 0($v0); jal show` and then, after rebuilding the same address, `lw $a0, 4($v0); jal wrap`. The m2c draft
passed `wrap()` nothing: `wrap` had matched as `s32 f(void)`, because it only hands `$a0` on to a loader that takes it,
and a pass-through needs no parameter to be byte-exact. A matched definition's `(void)` is therefore no proof that a
caller passes nothing. The caller's own `lw $a0` is.

The source change, and the measured result before and after (scores, the judge used): in the app's judge the saved
draft scored 430. Passing `*(s32 *)((u8 *)(A) + 0x4)`, where `A` is the address in the first call's argument, scored 35
(decomp-repair `loaded_call_argument`). `symbol_address_local` on that child scored 0. Writing the table as an array
of 16-byte records (`t[i].header`, `t[i].data`, `t[i].bank`) with the argument also scored 0.

Failed experiments that would otherwise be repeated: the signature check against callees' matched C reported nothing
wrong, and the saved attempts on the function had stayed at 430 or worse without the argument.

What is controlled evidence and what is one function: the family has unit tests on a small synthetic function. The
scores are one function.
