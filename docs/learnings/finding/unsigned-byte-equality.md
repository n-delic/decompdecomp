---
title: unsigned byte equality
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

In a 583-instruction N64 state handler, the target uses `lbu` to compare a table byte with 2, while the draft's `char` table read used `lb`. Casting that one read to `unsigned char` preserves equality to 2 and improved the linked O1 diagnostic from 7,050 to 6,850. Changing the table declaration to unsigned gave the same score, but changes all its reads; the one-read cast confines the change.

The generated child scored 23,579 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
