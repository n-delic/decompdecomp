---
title: A global read between two member negations, and the steps before it on a collision handler
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 204-instruction N64 collision handler: a loop over 100 records of 0x88 bytes, a fixed-point distance test, and a
bounce that negates both velocities and copies a byte of a global table into the other record. The m2c-based saved
best sat at 4050; a rewrite by hand over a typed record scored 2870, then 1040 with the squared differences shifted
into their own locals.

The last three steps, each judged by the app's standalone judge:

| score | change |
|---|---|
| 940 -> 865 | the `s32 angle = 0;` carried round the loop and only passed to two `s16` parameters declared `s16`: it and the loop counter had each other's saved registers |
| 865 -> 595 | `dx = (u32) (dx * dx) >> 12;` written `dx *= dx; dx >>= 12;`, and the same for `dy` |
| 595 -> 0 | `p->velX = -p->velX; tag = gTags[3]; p->velY = -p->velY; p->unk79 = tag;` with `u8 tag`, where the draft stored `p->unk79 = gTags[3];` last |

The decomp-repair families `cast_reads_narrow` and `square_in_place` (extended) and `global_element_load_stage` (new)
make these three edits: run in turn from the 940 source, each reproduces the next source, and the last child scored 0
(RetroReversing/decomp-repair#2240; the app's pin is pending).

Failed: moving the whole store above a negation (3035 above both, 2550 between them: the loop's pointer base moved to
another member); one shared temporary for the two negations (530 to 550); a `do { } while (0);` around the first
negation (the table address then follows its load); the squares inline in the call's argument (2285).

One function. Why the scheduler keeps the second velocity's load behind the first store only with the read written
between them was not read in the compiler's source: a hypothesis.
