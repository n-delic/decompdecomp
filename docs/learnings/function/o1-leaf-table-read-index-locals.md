---
title: -O1 leaf: a two-dimensional table read needs its two loaded indexes in s32 locals
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 178-instruction leaf that looks one byte up in `u8 T[][24]` by two `s8` fields of a 5-byte record, then dispatches on
ranges of a second field. The saved best (1345) was under the project's `-O2` with inline helpers and one-shot wrappers.

The source change: plain C with the records and the table in their real shapes, an `s8` kind compared by ranges
(`kind >= 0x16 && kind <= 0x1A`, which gcc folds to the `andi`/`sltiu` pair), under `-O1`, scored 675 (saved best under
`-O1`: 1925). The last step was the read: `n = T[rec[i].d][rec[i].c];` loads the row first. With `col = rec[i].c;
row = rec[i].d; n = T[row][col];` and both locals `s32` it was exact (0). The same locals `s8` stayed 675; row loaded
first scored 50. Judged by decompdecomp's judge.

A leaf has no prologue to tell the level: judging both `-O1` and `-O2` was the whole clue.

decomp-repair `matrix_index_locals` makes the step (controlled: from the 675 source its first child scores 0).
