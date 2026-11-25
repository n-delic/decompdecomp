---
title: A prologue's record stores written one record at a time in member order
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 438-instruction N64 floor-height ray test (SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp32`) opens by filling two `Vec3f` probes (`top`, `bot`) from a parameter and constants, plus a `Vec3i box[2]`. The
first scheduler interleaves these stores. Earlier, a move-one-line hill climb over the 14 statements had settled on a
scrambled order (`bot.z, top.y, top.z, below, above, top.x, bot.y, bot.x, box[0].z, box[1].y, ...`) at 257, with
11 prologue rows still wrong.

The source change, and the measured result before and after (scores, the judge used):

The same stores written one record at a time, each record's members in member order, and the records in declaration
order (`top.x, top.y, top.z, bot.x, bot.y, bot.z, below, above, box[0].x .. box[1].z`): the prologue matched,
257 -> 210 (app's judge-worker). Where the scalar stores `below`/`above` sit in the run made no difference (210 at
the start, the middle or the end). `bot` before `top` scored 336. The new decomp-repair family
`record_store_run_order` (#1746) emits both record orders. No existing store-order family produced any child here.

Failed experiments that would otherwise be repeated:

The rest of the function (210) is the loop top. loop.c hoists only the first table address in RTL order out of the
loop (`threshold * savings * lifetime >= insn_count`, with the threshold falling by 3 per move), and the target's
choice of which table that is was not reproduced. Every tri-first spelling compiles to the same 620.

What is controlled evidence and what is one function:

One function. The lesson is about search, not the compiler: a hill climb over a statement order that the scheduler
reorders anyway can settle on a local optimum. Try the natural order first.
