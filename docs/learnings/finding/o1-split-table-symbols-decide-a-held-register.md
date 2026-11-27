---
title: o1 split table symbols decide a held register
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 1758-instruction N64 spawn list: one loop, then about 60 straight calls passing addresses in one table, under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The splitter gave a later address in that table its own `D_` symbol.

The source change, and the measured result before and after (scores, the judge used):

Written as its own `extern u8 D_x[];` the candidate scored 10805: the target holds `base + 0xF0` in a saved register and
builds the later address again at each of its two calls, and ours did the reverse. Written `(base + 0x150)` it scored 0
(the app's judge). Library: `array_address_rebase`.

Failed experiments that would otherwise be repeated:

A pointer local holding `base + 0xF0`, struct-typed arrays and `&base[n]`, dead stores to move CSE's flush: no change
(10805). An empty `do { } while (0);` before the group scored 960 but loads the call constants again.

What is controlled evidence and what is one function:

One function. That the base staying live to the later use is what gives the other address the register is a reading of
the local-alloc dump, not checked in the compiler's source.
