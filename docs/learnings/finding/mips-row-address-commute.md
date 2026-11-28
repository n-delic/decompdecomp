---
title: mips row address commute
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler calculated a nonnegative row offset from an unsigned byte and added it to a byte-table pointer. On its 32-bit MIPS profile, expressing the address as `(u8 *)(offset + (u32)base)` reduced the linked O1 diagnostic from 150 to 145. Casting the base first and writing the operands in the opposite order tied at 150. The generated child reproduces the measured source.

The changed form scored 25,628 under the saved `-O2` profile, worse than 20,033, so it was not submitted as the saved candidate and the profile was not changed. The app retained the O1 candidate as a diagnostic trial. This is one function observation, not an exact match or a controlled regression test.
