---
title: row address before index
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler calculated a byte-table row address and a separate index from already-computed locals. Moving the independent row-address assignment before the index assignment reduced the linked O1 diagnostic from 135 to 115. The existing `statement_slide` repair generated the same child. Before the preceding address changes, the same ordering had worsened an earlier diagnostic from 150 to 190, so its effect depends on the surrounding source.

The changed form scored 25,628 under the saved `-O2` profile, worse than 20,033, so it was not submitted as the saved candidate and the profile was not changed. The app retained the O1 candidate as a diagnostic trial. This is one function observation, not an exact match or a controlled regression test.
