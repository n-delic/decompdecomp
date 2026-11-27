---
title: A cast pre-decrement test compiles to andi where the target shifts
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 215-instruction object state machine (N64, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, its own profile).

Symptom: the only differing row is `sll $v0,$v0,0x10` in the target against `andi $v0,$v0,0xffff`, after
`addiu $v0,$v0,-1; sh $v0,0x14($s1)` on a `u16` field and before `bnez`.

Fix: `if ((s16)--p->timer == 0)` gives the `andi`. `p->timer--; if ((s16)p->timer == 0)`, `(--p->timer << 16) == 0`
and an `s16` field with `--p->timer == 0` (read elsewhere as `(u16)`) are each exact: 200 -> 0.
`(s16)(p->timer -= 1) == 0` is not (200). The library's 396 children left the 200 unchanged; decomp-repair
`narrow_step_test` offers the first two forms. One function.
