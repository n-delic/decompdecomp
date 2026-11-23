---
title: A block with three or fewer local quantities is allocated in qty order, not by priority
kind: finding
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3
evidence: hypothesis
date: 2026-11-23
---

Reading of local-alloc.c `block_alloc`, checked against the order in which `find_free_reg` was called for the blocks of
a 673-instruction function (KMC GCC 2.7.2, `-O2`, cc1 under lldb: breakpoint on `find_free_reg`, reading `qty_*`):

- A basic block with four or more local quantities is sorted by `qty_compare_1` (priority = floor_log2(refs) * refs /
  (death - birth)), shortest-lived first, then allocated in that order, each taking the first free register in the
  allocation order (v0, v1, ...).
- A block with three or fewer quantities is ordered by a hand-written exchange network (`qty_compare (1, 2)`,
  `qty_compare (0, 1)` over positions, which are qty numbers only before the first exchange), so the quantity born first
  (the load that starts the block) is allocated first and takes `$v0`.

A loop head `lw v0,0x38(s1); [lw a0,0x3c(s1)]; sll v1,s0,1; addu v1,v1,v0; lhu v1,(v1)` is allocated B -> v0, index
chain -> v1 in the three-quantity case and the other way round in the four-quantity case. The extra quantity was the
load of a second record field plus the sum computed from it, both in the head block: writing the field load before
the first test and the sum after it (a nested `if`, so the sum lands in the next block) moved the load's pseudo out of the
local allocator and gave the target's registers in all four loops (the layout stayed wrong in another way, see the
function's notes), which is the evidence for the reading. Hypothesis-level: one function, and the nested form changed
the delay-slot filling.

The same file explains why `reg_n_sets == 1` matters for the first scheduler (sched.c `birthing_insn_p` raises such a
pseudo's defining insn to LAUNCH_PRIORITY, so a value defined once is scheduled as late as possible; a local assigned
twice is not): a zero-extended call result used by two masks was scheduled after the argument reloads, the target had it
first. Not solved on this function (see the notes): the second set must not widen the pseudo's live range.
