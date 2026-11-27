---
title: stepped offset symbol stores record array
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-27
---

A 158-instruction N64 audio manager set-up: allocations, one two-pass loop filling two global task records and one
word of a second record array, a thread start. Saved best 990 (isolated judge equal to the app's), stuck there: each
field was a `char` array of its own plus a hand-stepped byte offset, `size` was `(s32) (f + 0xD0) - (s32) f`, and the
call after the loop sat in both arms of an `if` on the dead offset. No library chain went below 785.

Plain C with the record arrays and the SDK's task layout matched at once. From the saved best, the minimal edits:
stores as `records[i].field` with the record based before the lowest field the loop writes (at the lowest field the
offset-0 store is scheduled last, 330; 8 or 16 bytes lower is exact), the symbol difference written with the second
symbol (the compiler folds `(f + 0xD0) - f`), the difference written at its use, `do { } while (0)` fences opened and
the equal arms written once. Each alone is worth little; `symbol_record_loop` (decomp-repair) offers them as one child,
which scores 0 from the saved best.

One function is the evidence; the effect of the record's base is the part to expect elsewhere (hypothesis: sched.c
tells `symbol + register` from `symbol + constant + register` differently).
