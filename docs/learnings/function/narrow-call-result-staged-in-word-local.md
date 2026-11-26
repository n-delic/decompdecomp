---
title: a narrow call result staged in a word local keeps its extension
kind: function
compiler: gcc-2.7.2-kmc -O2
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
An 87-instruction N64 loop over two 0x184-byte records, gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2` (the project's
own; the function had been left on a gcc-2.8.1 per-function profile, under which it never went below 1010). One call
passes `get(p) - r[i].step` to an `s16` parameter, where `get` is declared to return `s16`.

The source change, and the measured result before and after (scores, the judge used):
As a record array with indexed member access: 200, with only `sll $v0,$v0,16; sra $v0,$v0,16` after the call missing.
`s32 v; v = get(p); put(p, v - x);` scored 120 (the `sll` one move late). `v = get(p); v -= x; put(p, v);` was exact.
The app's judge. decomp-repair `narrow_call_result_stage` offers both.

Failed experiments that would otherwise be repeated:
An `s16` local for the result, `(s16)` casts on the call or the whole argument, an `s32` or `u16` return type and an
`s32` parameter all stayed at 200: GCC folds the extension into the narrowing of the sum.

What is controlled evidence and what is one function:
The spellings are controlled (one judge, one function).
