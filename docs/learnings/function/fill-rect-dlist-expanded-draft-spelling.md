---
title: A returned-cursor display list whose saved draft spells its stores as byte-offset casts
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 48-instruction `Gfx *f(Gfx *gfx)` routine: six commands, one a fill rectangle sized from two `s16` globals. The saved
machine draft (m2c) scored 4465 (app judge). The match is plain `gCmd`/`gFillRect` writes on `gfx++`, which scored 560
under the project's KMC GCC 2.7.2 `-O2` and 0 under the profile above (a `compiler_experiments` trial, applied to the
function).

From the draft, by the library: `returned_gfx_cursor` 4465 -> 265, then `or_chain_order` -> 0 (isolated judge, under
the profile above). `returned_gfx_cursor` wrote no child before decomp-repair #2221: the draft spells its stores as
`*((int *)((char *)arg0 + 0))`, `(*(s32 *)((char *)(t) + 4))` and `__repair_record_` members, with a `register` cursor
local, and the family read only `M2C_FIELD`.

Controlled: the two-family chain on the saved draft. One function.
