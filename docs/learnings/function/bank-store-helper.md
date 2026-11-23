---
title: bank store helper
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

A 239-instruction controller-word handler (an N64 audio engine, KMC GCC 2.7.2): the coarse half (`value >> 7`) and the
fine half (`value & 0x7F`) of a 14-bit value stored into a banked table or, when the bank byte is 0xFF, a default
table, in each arm of an `if` chain. The saved best (3530) was m2c's output after a search: identity `inline_fn`
wrappers, a typedef for the 134-byte row, identical-arm `if`s, gotos and duplicated blocks. Its instructions were
nearly the target's; 106 rows were register choices.

The source change, and the measured result before and after (standalone judge, then the app: recorded matched):

- Every store is one call of `inline static void store(u8 i, u8 r, u8 b, u8 v) { if (b != 0xFF) { D1[b][r][i] = v & 0x7F; } else { D2[r][i] = v & 0x7F; } }`.
  The first call of each pair passes `arg3 >> 7`, the second `arg3 & 0x7F`: 3530 -> 0, and the source needs no scaffolding.
- Parameter types: all `u8` (`int`/`u32` helper parameters 12580). Arm order: `!=` with the banked table first gives the
  target's `beq` to the out-of-line default arm; `==` first gives the opposite branch (20050 with the other arm order).
- The mask: helper masks, every call drops its mask 9025; helper masks, every call keeps its mask 6995 (the helper
  not masking, the calls masking, is the same code); helper masks, the shifted call drops it and the other keeps it 0.
  A `lo = arg3 & 0x7F;` local before the second call instead of the argument: 1870 (the `andi` of the value moves above
  the index computation). Hypothesis, not read in `sched.c`: a mask in a call argument is computed before the branch in
  the delay slot, one inside the helper after it, and the first store's two arms then share a tail only with the
  helper's.

What the permuter lacked: no family wrote the repeated two-arm store as a helper, and no chain of the existing families
gets there (each of the eight copies alone is neutral). decomp-repair `bank_store_helper` (PR 1371) offers three
children by the mask's fate; the third is the match, from m2c-shaped direct stores.

What is controlled evidence and what is one function: the scores are one function; the family finds the helper from the
direct-store spelling only, not from the saved best's wrapper form (a scaffold-stripping step from that form is not
written).
