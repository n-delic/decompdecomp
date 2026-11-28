---
title: unsigned row address commute
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler formed a signed 32-bit byte offset from an unsigned loop counter and added it to a byte-table row pointer. On its 32-bit MIPS profile, expressing the address as `(u8 *)(offset + (u32)base)` reduced the linked O1 diagnostic from 115 to 110. The offset computation is unchanged; the generated child reproduced the result.

The changed form scored 25,628 under the saved `-O2` profile, worse than 20,033, so it was not submitted as the saved candidate and the profile was not changed. The app retained the O1 candidate as a diagnostic trial. This is one function observation, not an exact match or a controlled regression test.
