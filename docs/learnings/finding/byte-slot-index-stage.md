---
title: byte slot index stage
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler has two branches that load a byte index and use it to form a word-slot pointer. Staging each read in one shared local of the byte's promoted type improved the linked O1 diagnostic from 1,160 to 810. A byte-typed local instead scored 1,160. Holding the word-slot byte-array address in short-lived locals at the two assignments then improved 810 to 770; that child scored 21,373 at O2. Staging the remaining five byte indices and table addresses in short scopes then improved 770 to 530, with an O2 score of 21,513.

The generated child scored 20,768 under the saved `-O2` profile, worse than its saved 20,353; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
