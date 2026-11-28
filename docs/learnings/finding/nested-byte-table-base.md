---
title: nested byte table base
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler uses a byte-table symbol and an unchanged pointer to that same table in a nested lookup. The target uses the held address for the outer lookup and the symbol for the inner lookup. Exchanging those two equivalent byte-base spellings preserved the addresses read and improved the linked O1 diagnostic from 4,515 to 4,015.

The generated child scored 22,148 under the saved `-O2` profile, worse than its saved 20,754; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
