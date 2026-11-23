---
title: guard copy hoist conflict
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: the 51-instruction slot setter of
`slot-store-address-local-and-reassigned-cast.md`, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, at 15 after that
entry's steps. Three register rows: the post-loop `id = (u8) action` (`andi`) in `$a1` where the target has `$a3`, and
the `sll`/`sltiu` reading it.

The source change, and the measured result before and after (scores, the judge used): the late copy that opened the
`if (id < 31)` arm (`j = (u8) action;`, sharing the dead loop counter's local) moved above the guard:
`j = (u8) action; if (((u32) id) < 31) { ... }`. 15 -> 0 in the standalone judge and in the app (recorded matched).
decomp-repair `guard_copy_hoist` (#1288) emits exactly that child from the saved best.

Why (global.c, hypothesis the measurement agrees with): `find_reg` takes the first free register in `reg_alloc_order`
(`$v0,$v1,$a0..$a3`, ...); `$a0` (object) and `$a2` (value) are live, and `id` never conflicted with the counter's
pseudo, so it took `$a1`. Hoisted, the counter/copy pseudo (in `$a1`) is set while `id` is still live (its last read is
the guard), so the two conflict and `id` falls to `$a3`. reorg.c fills the guard's delay slot with the copy, so the
instruction order does not change: the target's `beqz; andi $a1,$t0,0xff` is exactly that slot.

Failed experiments that would otherwise be repeated: see the earlier entry (separate late local 260-305, fences,
`id` assigned inside the test 60, role sharing, toggles). The answer was not a new local or a fence but where the copy
sits relative to the guard: a register lost to a "first free" pick means a conflict is missing; make the competing
value live across the read.

What is controlled evidence and what is one function: one function; the family's unit tests are controlled.
