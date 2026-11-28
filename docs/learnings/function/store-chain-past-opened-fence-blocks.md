---
title: Stored sum read back and byte stores chained, hidden from split_store_chain by an opened fence's braces
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 26-instruction N64 fade update at 0x8004B578: bump a counter, add to a phase word, add a constant to a halfword and
store it in three fields, store `~(phase >> 12)` in three bytes, release the entity past a count. `sn-n64-281 -G0
-mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, the function's own profile. Saved best 220, the same with its
fences opened and `(u16)` cast removed 1075.

The source change, and the measured result before and after (scores, the judge used):

The app's judge: 0 with `F(0x3C) += K;` (later reads through the field), the three byte stores as one chain, and the
halfword stores as one chain. Typed struct members and `M2C_FIELD` casts both give 0. Holding the phase in a local and
reading the field back is the step that moves it: with the local, `lhu` is scheduled before the counter's store.
`split_store_chain` writes both rewrites, but its line reader stops at a `{ A = t; }` block, which is what
`scaffold_strip.unfence` makes of `do { A = t; } while (0);`. With those blocks written as statements, its first
all-together child is 0 from the saved best and from the stripped form (decomp-repair PR 2265).

Failed experiments that would otherwise be repeated:

- Statement order, local widths, a shared local for two sums, and `u16` increment in two statements: 490 to 2130.
- A greedy chain of library families from the stripped form (40 judged children): no child under 100.
- The same C is also exact with `-fno-schedule-insns` under the same compiler even with a phase local; that profile was
  not applied, since neighbouring functions of the same file match with scheduling on.
- Keeping a `(u16)` cast on a 32-bit local leaves one `andi` (100); the chain writes the value in place and does not.

What is controlled evidence and what is one function:

One function, measured on the app's judge worker. That sched.c treats a value read back from memory differently from a
user variable is a hypothesis, not checked in the compiler's source.
