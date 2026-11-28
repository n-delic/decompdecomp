---
title: scoped typeof base weight
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler assigned a table address to a scoped `__typeof__(table + 0)` pointer, then used it to calculate a real increment pointer. Wrapping only that assignment in one `do { ... } while (0);` reduced the linked O1 diagnostic from 235 to 225; two or three nested scopes tied at 225. The scope is allocation scaffolding. The scope repair reproduced the gain once it recognized the scoped local.

The same source scored 20,988 under the saved `-O2` profile, worse than 20,118, so it was not submitted and the profile was not changed. This is one function observation, not an exact match or a controlled regression test.
