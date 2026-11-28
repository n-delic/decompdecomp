---
title: Sprite drawing routine, filled vectors, stored vector read back, and where a step is written
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-28
---

A 245-instruction N64 routine that draws one textured sprite: it picks a texture index, fills two 12-byte vectors
with a memset and a third component by component, hands all nine floats to a `guPosition`-shaped call, then writes
four vertices and the display-list commands. `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`.

A typed rewrite from the assembly (`f32 v[3]` vectors, float literals, a `Vtx *` stepped with `vtx++`,
`GfxCmd *cmd = gDisplayListPtr++`) scored 610 first time, judged by the app's judge. Two placements closed it:

- `x = &mtx[index++]` as `x = &mtx[index];` then `index++;`: 610 -> 160 (the command word is stored before the new
  index is written back). Alone on the 610 source.
- `tex--;` moved above the `if`/`else` that picks a scale (it fills that branch's delay slot): 610 -> 450 alone,
  160 -> 0 after the split.

On the m2c draft (8240) the vectors were the first gap: `f32 sp20; memSet(&sp20, 0, 0xC);` has to be `f32 sp20[3]`,
and three stored-never-read stack words passed on from temporaries have to be one array read back into the call
(8240 -> 6905 -> 5850). On the exact source with those two shapes undone: 3357 -> 1581 -> 0.

Library families: `fill_extent_scalar`, `stored_stack_run`, `index_postinc_statement`, `local_step_hoist`.

Controlled: each score above is the judge's on the named source. That the compiler's scheduler is why the placements
matter is a hypothesis; this is one function. Not closed by any repair here: the draft's chain of vertex pointers
(`next = prev + 0x10`, the previous record's field standing for the next one's first) and its split display-list
cursor still have to be written by hand.
