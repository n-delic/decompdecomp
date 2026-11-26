---
title: o1 pointer alias array state machine
kind: function
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

A 148-instruction N64 state machine (34-entry jump table), gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1` (2358
under the project's `-O2`, 420 under `-O1` with the same saved source).

The source change: a saved best kept `u8 *q = &D_x;` at the top beside `extern u8 D_x;`, so its address was loaded
before the dispatch. Declaring `extern u8 D_x[];` and writing the symbol at each use, on the scaffold-stripped form,
is 420 -> 0 (neither alone moves it). `symbol_alias_array` offers it. A pointer local to a halfword global that the
target does keep in a saved register stays, declared before the record pointer (420 -> 0 by declaration order in a
hand-written source). A differing jump-table `lui` naming another symbol went away with the other rows.

From the machine draft the existing families already reach 0 under -O1 (`symbol_address_local`,
`join_tail_arm_split`, `global_scalar_record`, `guard_temp_sink`: 3521 -> 0): the profile was what was missing.

One function.
