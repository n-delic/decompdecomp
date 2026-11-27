---
title: a store moved three places in a run, in the expanded field spelling
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 67-instruction N64 object initialiser
(`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). Its entry block stores constants into one
record, `(*(s8 *)((char *)(arg0) + 0x7A)) = 2;` and so on. The saved best stood at 110: the registers of the constants
and the order of three instructions differed, everything else matched.

The source change, and the measured result before and after (scores, the judge used): statement order only, judged by
the app's judge. The `s32` store at `0x14` moves down past the stores at `0x7A`, `0x74` and `0x78`, and the last two
stores (`0x75`, `0x7F`) swap. Chained: 110 -> 100 (`0x14` one place), -> 20 (past the other two), -> 0 (the swap).
With the three-place move written first it is 110 -> 20 -> 0.

Why the permuter's draft path missed it: `statement_slide` (moves of up to four places) read only `M2C_FIELD` and one
other cast spelling, so it offered no child for `*(T *)((char *)(base) + off)`. `store_run_order` offers adjacent swaps
and moves to either end of a run, reached 100 and stopped. The `store_order_climb` strategy (the Deep Permuter's local
lane and the autopilot) does reach 0 from the same source (36 judged steps, measured by calling it with the judge).
`statement_slide` now reads the spelling (decomp-repair PR 1986, release 0.6.270), and its chain alone reaches 0.

Failed experiments that would otherwise be repeated: none beyond the stall of `store_run_order` at 100.

What is controlled evidence and what is one function: the scores are from one function. The module's tests are
controlled: the slide is offered for the spelling, overlapping cells keep their order and a call stays a barrier.
Why the scheduler wants this order is not explained here (not checked in the compiler source).
