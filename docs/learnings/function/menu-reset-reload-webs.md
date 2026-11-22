---
title: A save-menu reset: a volatile pointer reloaded twelve times, one local per load, and a table read staged after the first store
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---
What the function (or case) looked like and the exact compiler and flags:

An N64 save-menu reset, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, 136 instructions, saved best 165 with 21 register
rows. After a few byte stores and a call it clears a record through `u8 * volatile gSaveMenuState`, which the target
reloads (`lui; lw`) before each group of stores, the groups' pointers rotating through `$v0`, `$v1`, `$a0`. It then
reads `sBonusObjectTable[slot]` inside a `do { } while (0)` fence after two byte stores. m2c named the pointers `p` and
`q` and reused them for every reload.

The source change, and the measured result before and after (scores, the app's judge):

Two edits, 165 -> 60 -> 0. (1) Every reload of `p` and `q` given a local of its own (flat declarations are as good as
nested scopes): 60, only `sll` against the first store left. (2) `s32 *tp = &sBonusObjectTable[slot];` before the
fence, the element's value read into `t` after the first store (`D = 0; t = *tp; R = 1; if (t != 0)`): 0. Library
families `pointer_reload_web_split` (1) and `element_read_stage` (2); `prove_chain` from the saved best reaches 0 in
those two steps.

Failed experiments that would otherwise be repeated:

Splitting a single web of `p` alone scored 80 and `reused_temp_split` offers nothing (scalar locals only, one web per
child). For (2): the pointer alone 370 (the load then follows both stores and the branch has no delay slot), the read
placed before the first store 270, directly before the `if` 370, no outer fence 270, `slot` multiplied by hand 370. The
loop-begin note of the fence puts a dependence of the first insn after it on everything before it (sched.c; hypothesis for
why the shift had to be computed outside).

What is controlled evidence and what is one function:

One function, confirmed by the app's judge. The register-rotation reading (each load a short local pseudo, allocated by
local-alloc rather than one long global pseudo) is a hypothesis; the score is measured. The match relies on the fence
the saved best already had and on the `tp`/`t` locals.
