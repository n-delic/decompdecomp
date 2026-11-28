---
title: Compare tree with a shared case label
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A MIPS audio dispatcher had a nested compare tree over a masked MIDI status. One leaf jumped to a note-off label inside the zero-velocity branch of the note-on leaf. Reconstructing a switch over the status while retaining that jump and the original case bodies improved the linked judge from 15571 to 9066; the app saved the result as its new best. The function remained open.

The target uses a compare-tree dispatch matching the switch form, but the compiler-pass cause of all remaining differences was not established. Existing switch families did not offer this tree because the shared label was nested in another leaf. A declared repair emitted a child that independently scored 9066; it checks signed selector and comparison forms, interval boundaries, external incoming jumps and loop breaks. Synthetic tests cover those guards, not additional game matches. The candidate already included separately recorded target-proved stack records and an initialized pointer correction.
