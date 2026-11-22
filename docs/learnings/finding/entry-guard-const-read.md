---
title: a guard's load above the register saves needs a const read, the copies after the branch and the loop-head load inside the do
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like: a 108-instruction N64 record walk with a 2.4 KB frame (a local struct holding a rotation
triple and an array of 48-byte matrices), `if (*list >= 0) { do { ... } while (*cur >= 0); }`. The target's first
instruction is the guard's `lh $v0,($a0)`, above `addiu $sp` and every callee-saved store; the loop's copies (`addiu
$s7,$sp,0x28` for the local array, `move $s2,$a0`, `move $s3,$a2`, `move $s5,$s4`) follow the `bltz`, whose delay slot
holds the last register save; in the loop `lh $a1` is followed by `addiu $a0,$sp,0x20` and only then `lw $v1,0x14($s5)`.
The saved best (240) had the guard read plainly, the copies written before it, and `s32 index = *p;` before the `do`.

Why, read in the GCC 2.7.2 source (confirmed): sched.c `sched_analyze_2` adds a true dependence from every pending
store to a later load when `true_dependence` (rtlanal.c) says they may conflict. The register saves are
`(mem (plus sp N))`; `rtx_varies_p` exempts only the frame and argument pointers, so they count as varying, and the
`MEM_IN_STRUCT_P` exemption needs a fixed address, so a struct-field read through `$a0` still depends on all of them
(tried: a `struct` cast on the guard changed nothing). The first line of `true_dependence` returns 0 for an unchanging
read after a changing store: `*(const s16 *) arg0` makes the load unchanging and the second scheduler (backward,
ties by original order) then places it first. The skill's older note that a `const` pointer parameter changes the
prologue's load order is this fact.

The source changes and scores (the app's judge, standalone):
- saved best: 240.
- const read of the guard alone: 350; the parameter copy sunk into the guard body alone: 650 (905 with the local
  array's `base` sunk too, still without the const read); the const read with both copies sunk: 60 (one `lw`/`addiu`
  pair swapped in the loop head).
- `s32 index = *cur;` moved inside the `do` (declaration kept above): 0. Which spelling the copies take matters:
  the local array's address first, the parameter copies after the body's own `var = arg;` run (the other order: 15).
- Tried and no better: the three `local.rot[..]` stores in all six orders, all permutations of the loop head's five
  statements, the rotation word read three times instead of a local, a pointer local for `&local.rot[0]`.

The permuter step: decomp-repair `entry_guard_sink` offers each of the three edits and every combination, the whole
set last; on the saved best the chain reaches 0 in one step (`prove_chain.py ... --families entry_guard_sink`).

Controlled evidence and one function: the const-read mechanism is read in the compiler source and reproduced in the
second scheduler's dump (`-dR`); the effect of the copy sink and the loop-head load are one function's measurements
(hypotheses for why: the first scheduler ranks a pseudo's only set late, and the loop head's order follows luid).
The match relies on no scaffolding beyond the saved best's own (the `__repair_rows` label and `do { } while (0)`
fences are still there).
