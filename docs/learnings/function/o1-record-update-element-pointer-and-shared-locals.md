---
title: -O1 object update routines: plain C, one element pointer, one local across blocks
kind: function
compiler: gcc-2.7.2-kmc -O1 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-27
---

Three N64 per-object update routines (387, 468 and 561 instructions): a `switch` on a record's state over a typed
record array, spawning through an 18-argument callee that returns `u16`. Their saved bests (about 10,000 to 11,000)
were m2c output tuned under `-O2`. Plain C written from the assembly (typed records, a real `switch`, repeated spawn
blocks written out) under a per-function `-O1` profile scored 0, 1235 and 180 on the first compile, all judged by the
app's judge-worker.

The two remaining edits of the 468-instruction one:

- `T[S[i].idx].x`, `.y`, `.z` spelled in three statements became `p = &T[S[i].idx];` (1235 -> 130);
- a vector length declared in one block and another in a later block became one local (130 -> 0).

The 561-instruction one had `Rec *p = &table[e];` declared in four separate blocks; one function-level `p` (180 -> 0,
only register rows differed).

Repairs: `repeated_element_pointer`, `block_local_shared`, `block_local_merge` (decomp-repair). No older library child
improved either starting source; from them these families reach 0.

A saved best that scores about the same at `-O1` and its own level says nothing about the level: write the plain form
and judge that. Controlled only on these three functions.
