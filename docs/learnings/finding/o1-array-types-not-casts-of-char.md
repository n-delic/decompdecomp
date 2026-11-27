---
title: at -O1 a global needs its real array or struct type, or its address is hoisted
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

Fifteen functions of 175-293 instructions from one N64 game, each built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` as a per-function profile (the project default is -O2). The saved candidates read globals as `extern char X[]` plus casts and offsets, or as m2c's `M2C_FIELD`.

Declaring each global with its real element type (a record array `extern Rec D_x[]`, a 2D `u16 D_y[][4]`) gives the target's `lui; addu; lbu %lo(sym)` per access. A cast of a `char[]` (`((Rec *) D_x)[i].f`) makes loop.c hoist the symbol's address into a register: On one 194-instruction function (app judge) the cast form scored 4480; real types alone made each access take the target's form but scored 4725; real types with the two stack arrays in one struct scored 3875, and further edits (field stores, pointer and lifetime changes) reached 0. All fifteen matched from plain C written from the assembly, most within a few edits.

Searching source spellings under the project's -O2 profile: the earlier saved bests (4437-19376) did not move toward exact. `x = -x` on an `s8` loads `lbu` where the target has `lb`; `x *= -1` gives `lb` (200 -> 0; repair `narrow_negate_multiply`).

The type spelling is measured on one function; that the whole group is -O1 is verified per function by the judge.
