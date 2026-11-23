---
title: texture builder cursor and compare temps
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: a 715-instruction display-list builder
(a texture-load command sequence in four arms, an `if (flag)` / `else` pair around each, a size-classification chain
`(w == K && h == K)`), saved best 1140, `gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32`. Almost every remaining row was a
register choice that followed from a few scheduling and web decisions.

The source change, and the measured result before and after (app judge scores), in order:
- Scratch locals that held a value used once (`t = (x + 1) >> 1; v = t - 1;`) written inline, with the compare
  temporaries (`t = h == 0x40; if (t && w == 0x40)`) written as the chain's own comparisons, the two sides of each `&&`
  in the order most conditions use: 1140 -> 1245 -> 1120 -> 1060. A shared scratch kept a register in the target's
  constant's range, which also moved the reload of a stack-spilled byte to `$t1`.
- The global cursor read (`p = gGfxPtr;`) written after the unrelated packed-value store that follows it (three
  places): 1060 -> 880. The first scheduler hoists the cursor load to the head of the block otherwise.
- A result local shared by the two branches of an `if` split into one local per branch (library
  `store_tail_into_arms`): 880 -> 585.
- The cursor read plus its bump written after the packed store in the tail: 585 -> 465; the pair of
  `size_log(h)`/`size_log(w)` calls turned round to the other arms' order: 415.
- The cursor temporary shared by two arms split per arm (library `local_web_split`): 415 -> 0.

Library families: `cursor_read_after_store`, `call_pair_order_peers`, `compare_temp_cascade_inline` (new, decomp-repair
PR 1328); `store_tail_into_arms` and `local_web_split` already offered their steps from the states above. No scaffolding.

Failed experiments that would otherwise be repeated: renaming the shared temporaries of the last arm to the other arm's
names or dropping the `__repair_phase_` copies merged webs and scored 660 to 970; removing the `(unsigned)` cast on a shift
changed nothing; the call-order swap alone scored 830 (585 -> 830) and only pays after the cursor read moved.

What is controlled evidence and what is one function: all one function; the per-step scores are measured with the app's
judge, the scheduler explanation is a hypothesis (sched.c's backward list scheduling with load latency).
