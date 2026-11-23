---
title: Guards written as the counter's first test leave dead compare pseudos, and each takes a spill slot
kind: finding
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3
evidence: observation
date: 2026-11-23
---

The same 673-instruction function (KMC GCC 2.7.2, `-O2 -G0 -mips3`) had four loops whose entry guard m2c wrote
`if (len != 0)` after `i = 0;`. The target's frame was 24 bytes larger than ours and every spill slot after the first
sat at another offset: 31 differing stack rows with all registers right.

Cause (reload.c allocates a stack slot for every pseudo that has references and no hard register, even when the insn that
used it was later deleted): when the guard is the loop's own first test (`i < len`, written `if (len > i)`), the compare
is an `ltu` insn whose result pseudo is later folded to `len != 0` and deleted, but the pseudo keeps its reference
count and gets a slot, 8 bytes each, in pseudo-number order after the spilled pointers. `if (len != 0)` never makes the
pseudo. Writing exactly the loops' guards as `len > i` gave the target's frame (and the stack rows went to 0).

Measured: with the frame-struct shrink the existing families already propose, 454 -> 395 with four guards rewritten (three
of four gave 426: a frame of the right size needs the right count, which only the judge knows). decomp-repair
`guard_counter_compare` offers each guard alone, all together and each one left out.

A second cause of unexplained slot shifts: a `volatile` pad or an oversized local struct (a 12-byte struct of which only
the first float is used) puts the first spill slot 8 bytes higher; the target's slots start right after the last real
local. Hypothesis-level: counted by trial (shrinking the struct to its float moved the first slot from 0x104 to 0xfc).

Controlled evidence: one function. The mechanism (dead pseudo -> slot) matches the slot arithmetic of both builds.
