---
title: An index byte loaded into the counter of a later loop
kind: finding
compiler: gcc-2.7.2-kmc
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1` (this profile for the one function). For each of eight records it copies
the entry the record's own selector byte picks, calls a function, then searches each record's list for that byte.

The source change, and the measured result before and after (scores, the judge used):
- Two byte-array externs and m2c's offsets replaced by one `s8 T[4][8][34]`, plain loops: 1575 at -O1 (3215 at -O2
  -fno-strength-reduce).
- The same with `s8 T[4][8][34]` indexed `[0]`, `[1]`, `[k + 2]` instead of a struct: 755.
- `out[j] = T[i][j][T[i][j][0] + 2];` as `k = T[i][j][0]; out[j] = T[i][j][k + 2];`, `k` being the counter of the
  search loop below: 755 -> 0 (the app's judge). decomp-repair `subscript_load_stage`; it reaches 0 from the 755 source.

Failed experiments that would otherwise be repeated:
A struct `{ s8 sel; s8 num; s8 list[32]; }` makes the list's base the symbol plus 2, which the target does not have.
-O2 with strength reduction off or on never came near.

What is controlled evidence and what is one function:
The before/after pairs are controlled; the register explanation (one pseudo for the selector and the counter) is one
function and a hypothesis.
