---
title: promoted byte goto webs
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler reused one `__typeof__(*table + 0)` local for unsigned byte indices in two goto-connected paths. Control-flow reaching-definition analysis proved the assignment webs independent. Giving the second web its own local of the identical promoted type reduced the linked O1 diagnostic from 225 to 205; the generated child reproduced the result.

The same change scored 20,988 under the saved `-O2` profile, worse than 20,118, so it was not submitted and the profile was not changed. This is one function observation, not an exact match or a controlled regression test.
