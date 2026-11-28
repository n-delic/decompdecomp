---
title: goto increment pointer copy
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler computed an increment into a private local, copied its pointer to a shared local, copied the increment into a shared value, and jumped to a store. Loading directly into the shared value, copying the pointer, then incrementing the value reduced the linked O1 diagnostic from 175 to 170. The private temporary had no other use; the generated child removed its declaration and reproduced the score.

The same change scored 24,833 under the saved `-O2` profile, worse than 20,118, so it was not submitted and the profile was not changed. This is one function observation, not an exact match or a controlled regression test.
