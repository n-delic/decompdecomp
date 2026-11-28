---
title: paired table base weights
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler assigned a real table base in a decrement path. Wrapping that assignment in one one-shot scope tied the linked O1 diagnostic at 205; two nested scopes reduced it to 175, and three tied at 175. A generated combined child reproduced the result. The scopes are allocation scaffolding, and a greedy search needs the two-scope candidate because the first step is a tie.

The same change scored 20,973 under the saved `-O2` profile, worse than 20,118, so it was not submitted and the profile was not changed. This is one function observation, not an exact match or a controlled regression test.
