---
title: texture loader composite load macros
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 485-instruction N64 texture loader `Gfx *f(Gfx *gfx, u32 index, u8 variant)`: name checks that remap the index, two
`switch`es that turn width and height into tile masks, then an `if`/`else if` chain over the pixel size (4, 8, 16) that
loads a palette (`gDPLoadTLUT_pal16`/`_pal256`) and the image (`gDPLoadTextureBlock_4b`, `gDPLoadTextureBlock`).
SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64` (the function's profile already). A matched
sibling texture loader in the same project already wrote its commands as gbi macros.

The source change, and the measured result before and after (scores, the judge used):

From the m2c draft (27385, `fast_judge.py`, the app's judge): writing the composite macros by hand with a typed record
for the texture table was 3770; the third arm's clamp flags and the palette index read into a local before the size
switches 525; that local as `s32` (not `u8`, which masks it again at its use) 0.

The same match as library steps from the saved draft, each child picked by hand:

| step | family | score |
|---|---|---|
| arms as macros, `if` chain, palette read as a record member | `gbi_texture_load_chain` (new) | 845 |
| shared `goto` exit as `return` | `goto_shared_tail` | 435 |
| staged `x * 2` guard as one `&&` | `guarded_stage_join` (new) | 680 |
| range test as an early return | `guard_shape` | 210 |
| the two record pointers as one local | `pointer_local_merge` | 180 |
| third parameter as `u8` | `parameter_width` | 0 |

Failed experiments that would otherwise be repeated:

A greedy chain stops short: the `&&` step raises the score (435 -> 680) until the guard after it is an early return.
`default_into_then` also looks like a guard rewrite here but moves an initialisation into the arm (1565). The existing
display-list families write nothing for this draft (their cursor is a global or passed by address).

What is controlled evidence and what is one function:

One function. The unit tests are homemade drafts and control the rewrites, not the scores.
