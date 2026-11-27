---
title: An -O1 setup routine: trailing zero arguments and a word stored through a byte array
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 292-instruction N64 state reset (calls with 18 arguments, two loops over record tables), gcc-2.7.2-kmc
`-G0 -mips3 -mgp32 -mfp32 -O1`; its own profile was `-O2`.

The source change, and the measured result (the app's judge): the saved best scored 7091 under `-O2` and 3275 under
`-O1`. By hand, real record arrays for the two tables, `u16` loop counters, all 18 arguments of every call and `1.0f`
matched at the first compile. By repair, from the saved best with its one-shot fences removed (1432):
`stack_argument_tail` (600), then `asm_symbol_access_type` on the elements of an `extern char D[];` the target stores
words through (0). The second part is new: the family only read `*(T *)(...)` casts before.

Failed experiments: the same repairs on the fenced saved best stall at 1533, because the `do { } while (0)` fences keep
cse from sharing a constant between a call's argument and a later store (the `$s1` and 8 frame bytes the target has).

Controlled evidence: the family's unit test. The scores are one function.
