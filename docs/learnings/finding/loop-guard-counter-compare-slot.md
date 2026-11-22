---
title: a loop's entry guard written as the counter's compare gives the frame an unaccessed 8-byte spill slot
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A 401-instruction N64 resource loader (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`: a scan loop over 0x44-byte records
taking five maxima, eight buffer allocations, a second loop copying and converting each record) sat at 22 for days. Every
instruction matched; the only rows left were `addiu $sp,$sp,-0x98` against the target's `-0xa0` and the ten save/restore
offsets. All nine spilled values were at the target's offsets, and the target's extra 8 bytes sat after the last one with
no load or store anywhere. Any padding local (an unused `double`, a `u32[2]`, before or after the header, in a nested
scope) shifted the spill slots by eight and scored worse: named locals are allocated before reload's spill slots.

The source change, and the measured result before and after (scores, the judge used; app judge worker, then
`compile_and_diff`):

| edit | alone | combined |
| --- | --- | --- |
| saved best | 22 | |
| entry guard `if (var_s6 != 0)` (count, `unsigned long`) over the first loop written `if (var_s3 < var_s6)` (the counter, 0 there, the loop's own test) | 355 (frame right, four saved registers rotated) | |
| the second loop's record-base temp `temp_s1_2` written as the first loop's packed-size maximum `var_s1` | 22 | 0 |
| the saved best's identical-arm `if (header.packedSize) { x } else { x }` dropped | 22 (no change) | |

The slot is the compare's temporary. With the counter in a register, the guard is `(set t (ltu zero_holder count))` then
a branch on `t`; `combine` folds the compare into the branch and re-emits the zero holder's set where the compare was,
and `t` keeps references that no instruction has any more. reload1.c's `alter_reg` gives a pseudo with `reg_n_refs > 0`,
no hard register and no equivalence a slot, in ascending pseudo order after every real spill (so the slot is last and
shifts nothing). Controls on small functions with the same cc1 (twelve live values, a counted loop): the slot (+8,
no extra accesses) appears for `n > Z` and `Z < n` against a register `Z` holding 0, signed and unsigned, and for none
of `!=`, `==`, `>=`, `<` against it, nor for the literal `0`; the earlier finding on `late_zero` is the same case.
That the stale count is what keeps the slot is read from combine.c and reload1.c and is a hypothesis; the frame figures
are the evidence.

The guard also moves the allocation: cse maps the counter to the longest-lived register that holds 0 (not the lowest
numbered, not the first set: `make_regs_eqv` prefers the pseudo whose last use is latest, here the mesh-count maximum),
and combine re-emits that register's zero at the compare, so its live range starts at the guard and its priority rises
(`floor_log2(refs) * refs / live`, 7 refs over 104 became 8 over 63). That rotated four saved registers. The second edit
makes the second loop's temp share the packed-size maximum's pseudo, which lifts that pseudo's priority back over the
rotated ones; `disjoint_local_merge` on the guarded text finds it in one step (it chose the loop counter when started
from a different ordering of the zero initialisers, 85 -> 35, so the choice of merge target is the judge's).

Failed experiments that would otherwise be repeated:

A fresh `zero` local (set at entry, before the guard, declared first or last): the compare then picks the maxima's
register, not `zero`, the slot appears and the allocation is the same 355. `zero` as the compare operand of the *second*
loop's guard: combine folds its set into the compare and no slot appears. `n >= zero + 1` and `!=`: no slot. Another
variable as the compare operand: the same 355, whichever it is, because cse canonicalises it. Retyping the maxima
(`u16`, `s32`): worse (1460-2052). Hoisting the mesh multiply above the earlier allocations to shorten that maximum's
range: 645. A late use of `zero` to make it the longest-lived holder: it then lives and takes a saved register (1252-1617).
Scanning 18 positions for each of three zero initialisers was started and not needed.

What is controlled evidence and what is one function:

The slot condition (a `>` compare against a register holding 0 before a loop) is controlled on small functions with the
same cc1. The rotation, and that merging the record-base temp into the packed-size maximum repairs it, is one function.
decomprepair `loop_guard_counter_compare` emits the guard and the combination with each `disjoint_local_merge` child.
