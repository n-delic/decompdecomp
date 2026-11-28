---
title: Particle quad builder: pointer set between two guard tests, a Vec3 point, negate after convert
kind: function
compiler: sn-n64-281 -O2
evidence: observation
date: 2026-11-28
---

A 205-instruction N64 display-list builder: a loop over 500 typed records (tested at the top, `u16` counter) that writes
four vertices per live record. Compiler `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`.
A machine draft sat at 12471 after more than a hundred attempts; a plain rewrite with typed records and `Vtx` stores
scored 2696, then the app's judge gave:

1. `while (1)` with the counter test and `break`, and `-(f32) p->y / 1.8f` (the target has `cvt.s.w; neg.s`): 1127.
2. The buffer global stored in each arm of the `if`/`else`, not through a local: 921.
3. `if (a && b && c) { p = records; if (d) { ... } }`, the record pointer set between the guard's tests (the target
   computes `%hi(records)` before the last test and finishes it in that branch's delay slot): 6.
4. The point declared `f32 pos[3]` with `pos[0]` and `pos[1]` used (the frame is 8 bytes larger): 0.

Taken back one at a time from the exact source: `pos[2]` is 6, `-p->y` is 200.

Library families (decomp-repair): `guard_conjunct_hoist_split`, `float_pair_vec3`, `float_negation_cast_order`.
`prove_chain.py --families` of these three reached 0 from the state before step 3 (921 -> 6 -> 0) and from the
`-p->y` spelling (200 -> 0). Step 2 is already offered (`join_tail_arm_inline`, `override_store_split`).

Failed experiments: `pos[3]` without the guard split scored 915 (barely below 921); the existing `unused_frame_padding`
child did not improve the 6.

This is one function: controlled one-edit comparisons, not a rule measured across games.
