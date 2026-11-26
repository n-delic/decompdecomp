---
title: A row pointer from a pointer table wants a local before its row is indexed
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 131-instruction distance-to-waypoint routine, gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1` (a per-function profile).
It reads a byte through `TABLE[GLOBAL][o->kind * 2]` where `TABLE` is an array of byte pointers, and indexes a row
table with that byte.

The source change, and the measured result before and after (scores, the judge used):
Plain C with the nested subscript was 90 (registers and two reordered rows: the index byte was loaded before the table
slot, and the slot's address went through `$at`). With `tbl = TABLE[GLOBAL];` on the line before, 0 (the app's judge).
The library family `nested_row_pointer_local` (decomp-repair 0.6.170) writes it.

Failed experiments that would otherwise be repeated:
The saved best (2936, `-O2`) and a note-recorded 840 under `-O1` carried search scaffolding; the plain source with the
data declared in its real shape (record arrays, a struct for the object) was closer than either at once.

What is controlled evidence and what is one function:
One function. That the table load order is the first pass to differ is an observation, not read in the compiler.
