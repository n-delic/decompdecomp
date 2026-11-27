---
title: late stack literal set before store
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: verified
date: 2026-11-27
---

A 16-statement N64 object setup (KMC GCC 2.7.2 `-O1 -g`, also judged under the `sn-n64-272-0001` profile) whose
eleven-argument call passes a constant as the last (stack) argument. The target loads that constant into a register
before an earlier halfword store (`0x172` field, set to `0x64`) and stores it from there; with the literal written
inline in the call the compiler loads it late, next to the other argument set-up.

Source change, measured with the app's judge (`fast_judge.py`): `s32 flag; flag = 0x47;` written one statement above the
store, the call's last argument `flag`. Saved best 200 -> 0 (the pre-edit C re-judged at 215 here).

Not a new family: `integer_argument_early_local` (decomp-repair, since 0.6.118) already makes this exact child.
`prove_chain.py --families integer_argument_early_local` reached 0 in one step from the pre-edit C (16 children, the
set placed one statement back is the exact one; two or three back score 431). With `--families all --children 40` the
step stopped at 215 ("488 of 528 children cut off"): the generator lists `local_weight_ladder` (187) and
`call_weight_ladder` (81) first, so a cap loses the 16 children of this family. `--children 600` found it. A search
with a small child budget should name the families a symptom points at (a literal argument loaded late) instead.
Regression test: decomp-repair `tests/test_integer_argument_early_local.py`, the M2C_FIELD statement shape.

The set must go directly before the store the target loads ahead of; one more statement back (before the `0x17A`
store with the call in it) is wrong by 400 rows.

Controlled: the exact child is reproduced from the pre-edit C by the family. One function.
