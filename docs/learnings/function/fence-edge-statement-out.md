---
title: a fence that ends one statement early lets two loads above a byte store (60 to 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 62-instruction N64 object spawner: a counter clear, two calls, three halfword stores of one constant inside a
`do { } while (0)`, three byte stores, two words shifted right by 2 in place, then a second record filled in. The saved
best scored 60 with one instruction group out of order: the target loads the two words above the first byte store, ours
stored the byte first.

The source change (60 to 0, the app's judge): the third halfword store moved out of the fence, after `while (0);`. The
first insn after the closing loop note then is that halfword store, not the byte store, so the byte store is no longer
the point later loads can't pass (hypothesis from `sched.c`'s loop-note dependences, not traced in the dumps).

Failed (same judge, same seed): no fence at all 700 (both loads rise above the first call's result use); the fence
widened over the first byte store 120; loads in locals before the byte stores 265 (the first shift is scheduled early);
both shifts before the byte stores 265. All 426 children `prove_chain.py --families all` offered for the saved best
stayed at 60 or worse.

Library family: `fence_edge_move` (`fence_edge_out`, `fence_edge_in`); its first child of the saved best is exact.
One function.
