---
title: switch labels and unnamed jump table
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function looked like: a 100-instruction display-list leaf with a 35-entry jump table. The splitter named the
table `D_...` (it sits in data no segment lists), so the m2c draft cut the switch and earlier attempts left most values
to `default:`. The compiler is `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, as its matched neighbours (the project
default is -O2).

The source change, measured with the app's judge: plain C with array-of-record globals, one block-local packet pointer
per display-list command, and the four arms holding every value the table sends to them: 1210 under -O2 became exact
under -O1. With only one value per arm the same source scored 1: every instruction equal, only the table differed.
`switch_tables.label_variants` (decomprepair, family `switch_table_labels`) takes that score-1 source to 0.

The app now supplies a dispatched `D_` table to m2c under its `jtbl_` name (`draft_data.read`): the draft then has the
whole switch (16 cases instead of a cut switch).

Failed experiments: the -O2 profile with any spelling tried by earlier agents (best 485, with an unfaithful
`& 0xFF` on a command word).

Controlled evidence: the unit tests of both changes. The scores are one function's.
