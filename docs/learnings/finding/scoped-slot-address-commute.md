---
title: scoped slot address commute
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler formed a word-slot pointer from a scoped byte-table base and an index loaded once from an unsigned-byte table. On its 32-bit MIPS profile, expressing the address as `(s32 *)((index * 4) + (u32)base)` reduced the linked O1 diagnostic from 145 to 135. The index is between 0 and 255, so scaling by four cannot overflow. A generated child reproduced the measured source.

The changed form scored 25,628 under the saved `-O2` profile, worse than 20,033, so it was not submitted as the saved candidate and the profile was not changed. The app retained the O1 candidate as a diagnostic trial. This is one function observation, not an exact match or a controlled regression test.
