---
title: Text-glyph display-list builders under -O1: gbi macros, and a packed index split before it is scaled
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Leaf functions of about 145 instructions that take a `Gfx **`, a position, a glyph index, a CI4 sheet and a palette,
and write `gDPLoadTLUT_pal16`, `gDPLoadTextureTile_4b`, `gSPTextureRectangle` (Fast3D/F3DEX halves 0xB4/0xB3) and
`gDPPipeSync`. A family of them differs in glyph size and sheet width. The function profile was `-O2`; the target has
no frame, no delay-slot work and no scheduling, and `-O1` is the profile (the same source scored 23490 under `-O2` and
2555 under `-O1`). `-O2 -fno-schedule-insns -fno-schedule-insns2` scored the same as `-O1`.

The source change, and the measured result before and after (scores, the judge used):

- The machine draft's raw words become the SDK's macros on a local `Gfx *gfx = *gfxp;`, stored back once at the end.
- Position parameters `u16`, glyph index `u32` masked in place (`index &= 0xFFFF;`), column and row locals `u16`.
- 8-pixel glyphs (143 instructions): `t = index >> 4; s = index & 0xF; t *= 8; s *= 8;` is exact. The one-expression
  form `s = (index & 0xF) * 8; t = (index >> 4) * 8;` scored 265 (520 with `s32` locals).
- 10x14 glyphs (148 instructions): the one-expression form `s = (index & 0xF) * 10; t = (index >> 4) * 14;` is exact.
- The `index & 0xF` / `index >> 4` pair is repaired by decomp-repair `field_extract_scale_split` (520 -> 0 in one
  child). The draft's pointer-argument cursor chain is `gbi_macro_param_cursor`: 9775 -> 1585 on a 153-instruction
  sibling (hand-flattened saved candidate, not submitted).

Failed experiments that would otherwise be repeated:

`s = (index & 0xF) * 8` with the scale written as `s <<= 3` straight after both extractions, `u16` index parameter, and
the scale only in the macro arguments (`s * 8`) all stayed between 250 and 2500.

What is controlled evidence and what is one function:

The scores are the app's judge on two matched functions; the `-O2`/`-O1` difference is one function.
