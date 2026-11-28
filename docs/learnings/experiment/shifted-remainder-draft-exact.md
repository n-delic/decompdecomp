---
title: A draft stuck at 780 with a shifted remainder read as a division was one existing library child from exact
kind: experiment
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
families: shifted_dividend_remainder
---

What the function (or case) looked like and the exact compiler and flags:

A 25-instruction N64 leaf that sets a few fields of an object, steps two counters and picks an animation frame from one
of them. The m2c draft ended `(n >> 2) - (((count + 1) / 132) * 0x21)`, where `n = count + 1`. It had 17 attempts by
three agents and its best score was 780. The profile was already the SN one above.

The source change, and the measured result before and after (scores, the app's judge):

`(n >> 2) % 33` for that expression: 780 -> 0. decomp-repair `shifted_dividend_remainder`, run on the unchanged draft,
gives one child (`% 33`), which also scores 0 in the isolated judge. The app's draft and local lanes both include the
module in release 0.6.466.

Failed experiments that would otherwise be repeated:

None: the first hand edit was exact. Before spelling variants on an open draft with `/ N` beside `>> k`, run
`prove_chain.py --families shifted_dividend_remainder,folded_shift_quotient,magic_high_divide` on it: a module added
after a draft was made may not have been run on that function (a hypothesis here; why the app had not taken the child
was not traced).

What is controlled evidence and what is one function:

One function. No new repair was needed.
