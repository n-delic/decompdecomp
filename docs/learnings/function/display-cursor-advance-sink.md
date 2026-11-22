---
title: display cursor advance sink
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like: an 89-instruction N64 display-list builder (cursor global advanced `G = p + K` between command words, KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`), saved at 4900 from m2c.

The source change, and the measured result (the app's judge-worker): `*(&arr + i * 4)` on a 4-byte element array scaled twice (use `arr[i]`), the callee's stray extra arguments dropped, the cursor re-read after the call, the words through a record, `or` chains in the target's grouping: 4900 -> 270 (library families, all found). The last two steps tied at 270 alone and gave 0 together: the third cursor advance one store later (after the `0xE3000A01` word) and a scalar's `size = 2;` init moved past the last two loads. The constants hoisted to the entry block are ranked by local-alloc.c by refs * floor_log2(refs) / live length, ties by lower quantity number (read in local-alloc.c qty_compare_1); the stores' sched1 order decides each constant's live length, so one store's position swapped two constants' registers. (That the advance's position is what moves the store order is a hypothesis; the measurements are the evidence.)

Library: statement_group_order (decomp-repair, after PR 1223) treats an advance and a store through its pointer as independent and offers pairs of disjoint moves as one child; `prove_chain.py --families all` from the saved best then reaches 0.

Failed experiments: permuting only the constant stores (5040 orders) never changed the registers because the advance's position was the lever; a hill climb over single-statement moves found the fix in one round.

What is controlled evidence and what is one function: one function; the chain result is a controlled run of the exact judge.
