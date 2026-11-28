---
title: A two-command display-list macro keeps one command pointer, set twice
kind: function
compiler: SN GCC 2.8.1 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 17-instruction N64 routine builds two display-list commands through a pointer parameter and passes the advanced
pointer to the next builder. The target has `move $v1, $a0` before each command, the second command's constant in
`$v0` after the first command's stores, and the second copy of the pointer in the same register as the first.

The source change, scored by the app's judge: the machine draft (stores at offsets 0, 4, 8, 12 and `p + 16` passed on)
1260; each command in a block of its own with its own `_g = p++` 920; one block whose single `_g` is set once per
command (what libultra's two-command macros such as `gSPLoadUcodeEx(p++, ..)` expand to) 0. decomp-repair
`shared_gfx_cursor` (with `returned_gfx_cursor` reading a cursor handed to a call) writes it from the draft.

Failed experiments: a capped `prove_chain` over every family from the draft stopped at 885
(`stored_sum_param_accumulate`), with most of that step's children unjudged.

Controlled evidence: the three sources above, under one profile. That a second set of one pseudo is what stops the
first scheduler hoisting the second constant is a hypothesis; the compiler passes were not read. One function.
