---
title: float argument dispatcher draft constants
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a `switch` over a `u16` argument with ten
table entries, entry 0 empty, each other arm one call with eighteen arguments (floats on the stack) and one more call.
Built at `-O1`; the function was set to `-O2`.

The source change, and the measured result before and after (scores, the app's judge): the stored machine draft under
`-O1` scored 2275. It passed one float stack argument as its bit pattern into an `M2C_UNK` parameter and had no
`case 0:`. `float_argument_bits` alone: 250. With `switch_range` (`case 0:` on the default arm) as well: 0. The app's
profile probe now offers the two together under the old level (`draft_constants`).

Failed experiments that would otherwise be repeated: the saved C (float constants held in locals shared between arms,
case labels off by one, one wrong constant) stayed near 23000 under `-O2` and 3000 under `-O1`; no work on it was
needed once the draft was used.

What is controlled evidence and what is one function: one function.
