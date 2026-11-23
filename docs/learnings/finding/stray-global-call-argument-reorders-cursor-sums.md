---
title: stray global call argument reorders cursor sums
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 525-instruction N64 display-list builder (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) walks two linked lists and,
for each visible entity, writes two commands at the global cursor (`cursor->w0 = ..; cursor->w1 = ..; cursor++;` twice)
and calls a helper. m2c had passed the cursor as the helper's second argument (`helper(e->id, sDisplayListCursor);`,
prototype `s32 helper(s32, void *);`) because `$a1` still held it. The helper's matched source takes one argument and
reads the cursor itself. The last nine rows were all in that write: the target computes `gp + 8` before `gp + 16`
with `gp` in `$a1` (the `gp + 16` sum ties to `gp`, which dies there); the candidate scheduled `gp + 16` first and put
`gp` in `$a2`.

The source change, and the measured result before and after (scores, the judge used):

Dropping the stray argument from the call and the prototype: 55 -> 0 (fast_judge and the app's `compile_and_diff`).
decomp-repair `stray_global_call_argument` offers it; `prove_chain.py --families stray_global_call_argument` reaches 0 from
the 55 source in one step. The earlier part of the run (3551 -> 55, a hand rewrite) started with m2c's frame union;
`frame_view_locals` writes that union out (3551 -> 3099 alone).

Failed experiments that would otherwise be repeated:

About twelve spellings of the cursor block (record members with `cursor = g + 1`, `p1 = cursor++; p2 = cursor++`,
staged colour words, reordered stores) compiled identically, and a prove_chain over eleven cursor families found
nothing: the order is decided by the extra `$a1` copy, not by the block. Hypothesis (from sched.c, not traced): the copy
to `$a1` is a second consumer of `gp + 16`, so the first scheduler (`birthing_insn_p`, launch priority) no longer places
that sum just before its store, and the allocator then ties the other sum to `gp`.

What is controlled evidence and what is one function:

One function. Check a callee's matched source (neighbours.py lists it) before trusting an m2c prototype with a trailing
global argument.
