---
title: n64 o1 texture tile halfword corners
kind: function
compiler: gcc 2.7.2 KMC -O1
evidence: observation
date: 2026-11-26
---

A 135-instruction N64 display-list emitter (gcc 2.7.2 KMC): a two-state switch; one arm loads a 32x32 texture tile of
an 8-bit sheet, the cell picked by `v % 7` and `v / 7`, then sets a second tile size. Saved best 2340 under `-O2`.

Written as the gbi macros (`gDPLoadTextureTile` and `gDPSetTileSize`) under `-O1`: 1900. The corners as halfword
locals are exact:

```c
u8 s = v % 7;
u8 t = v / 7;
s16 x0 = s * 32;
s16 y0 = t * 32;
s16 x1 = x0 + 32;
s16 y1 = y0 + 32;
gDPLoadTextureTile(..., x0, y0, x1 - 1, y1 - 1, ...);
```

The target computes `s << 5` before the first command and adds 0x20 after it, keeping `+ 32` and `- 1` apart.
`call_span_locals` (decomprepair) does it from the written-out corners in one step (1900 -> 0).

Failed (each tried in grids of a few hundred): `s * 32 + 31` written out (1900), `(s + 1) * 32 - 1` with `s32 x = s * 32`
(285, the best with word locals: registers of the two corners swapped), casts on the sum (485), an inline loader
function (1790), `-O2` with scheduling off, `sn-n64-272` and 2.8.1 profiles. `s32` locals never reached it.

One function; evidence is the app's judge.
