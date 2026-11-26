---
title: a while(1) over a masked word counter is a narrow counter's for loop
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 97-instruction state machine on a 100-byte
record; one case scans a byte table for the first zero among six entries. The project default is -O2; the function is
-O1 (per-function profile, from an exact `compiler_experiments` trial).

The source change, and the measured result before and after (scores, the judge used): m2c's `while (1)` with a word
counter, a masked copy and two breaks (saved best 450 at -O2, 440 at -O1) written as `for (i = 0; i < 6; i++)` over a
`u8 i` gave the app's judge 0 at -O1 (decomp-repair `masked_while_counter_for`). A clean typed rewrite scored 720 at -O2
and 0 at -O1.

Failed experiments that would otherwise be repeated: widening only the counter to `u8` and keeping the `while` form
stayed 440; stripping the identical-arm `if`s and typing the timer `s16` changed nothing.

What is controlled evidence and what is one function: the family has tests on made-up source; the match is one function.
