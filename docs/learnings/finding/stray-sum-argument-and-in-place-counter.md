---
title: A stray `v + 1` call argument hid an in-place counter step; dropping it closed a scheduler near-miss
kind: finding
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:
A 234-instruction N64 scene init at 10 under `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`
(160 under its default fp64 profile). m2c had written `id = arg0->f; if (id == 0) { id = D_X; ...; arg0->g = id + 470;
D_X = id + 1; notify(arg0->g, 0xDAA, 0, id + 1); ... }`, where `notify`'s matched definition takes three parameters.
The target has `lw $a3, D_X; ...; addiu $a3, $a3, 1; jal notify; sw $a3, D_X` and the entry test in `$v0`.
Several agents had read the mechanism (`docs/learnings/experiment/multi-set-load-without-entry-register.md`): sched.c
`birthing_insn_p` boosts the set-once load of `D_X` unless `id` has two sets, and the reused entry-test local that gave
it two sets also put the entry test in `$a3`.

The source change, and the measured result before and after (scores, the judge used):
Three edits together, 10 -> 0 (isolated `judge-worker` with the -mfp32 flags, then the app's trial, applied with
`suggest`): drop the fourth argument (`signatures.py` flagged it as an m2c guess), write the store as the counter
advanced in place (`id++; D_X = id;`), and write the entry test inline (`if (arg0->f == 0)`). The in-place step is the
second set of `id` that every earlier attempt was looking for; it had scored 560 only because the stray `id + 1`
argument kept `T = id + 1` alive into `$a3` beside it. decomp-repair `arity_drop_store_step` (0.6.26) offers the
three children: drop (35), drop + step (35), drop + step + entry inline (0).

Failed experiments that would otherwise be repeated:
Drop alone 35; drop with the entry inline but the store kept as `D_X = id + 1` 240; the step with the stray argument
kept 560 (earlier notes); a re-read `id = D_X` after the store 10 / 210.

What is controlled evidence and what is one function:
One function. The rule worth keeping: run `signatures.py` first; an extra argument that is a sum of a live local keeps
that sum's own pseudo alive and hides the in-place spelling, so a "no shape gives two sets" verdict from searches made
with the stray argument in place is not evidence.
