---
title: A compared load repeated in an arm is a local
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 232-instruction leaf fade state machine over an array of 100-byte records (N64, `gcc-2.7.2-kmc
-G0 -mips3 -mgp32 -mfp32 -O1`, applied as this function's own profile; its matched neighbours are -O1 too).

Symptom: the target loads a byte into `$a1`, adds 8, tests it with `sltiu $v0,$a1,0xff` and stores `$a1` in the other
arm; `if (r.a + 8 >= 0xFF) r.a = 0xFF; else r.a = r.a + 8;` computes the sum again in the arm.

Fix: `{ u32 t = r.a + 8; if (t >= 0xFF) r.a = 0xFF; else r.a = t; }`. With the rest of the source plain (the records
as one struct array, not parallel `D_...[]` byte arrays) this was 1565 -> 0 in the app's isolated judge; the library's
chain from the 1565 source stopped at 665. decomp-repair `condition_value_local` offers the local.

Also seen: a best saved under -O2 (2070 after 57 attempts, with fences and an inline helper) was a dead start; the
plain source under -O1 was within a few rows at the first compile. One function, not a controlled experiment.
