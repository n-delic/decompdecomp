---
title: display list text glyph parameter shapes
kind: function
compiler: gcc-2.7.2-kmc
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Five text-glyph display-list builders (153 to 208 N64 instructions) that write a TLUT load, a 4-bit or 8-bit
`gDPLoadTextureTile` and a texture rectangle through a `Gfx **` argument. KMC gcc 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O1` (their project profile said `-O2`; applied per function).

The source change, and the measured result before and after (scores, the judge used):

The SDK macros on a block-local `Gfx *` copy of `*arg0`, `s16`/`u16` parameters, `u16` column and row locals, and every
parameter m2c left out as a placeholder, so the stack arguments keep their offsets. The sub-tile builders need the tile
line written as gDPLoadTextureTile writes it, `(((lrs - uls + 1) + 7) >> 3)` (`>> 1` first for 4-bit texels): it
folds to `(width + 7) >> 3` but leaves an eight-byte stack slot (frame 0x28 against 0x20, 816 and 809 to 0).
All five hand-written sources were exact on their first judge call. From the m2c drafts under `-O1`: 10750 to 14827;
with `gbi_macro_param_cursor` alone 1465 to 4208; with `gbi_param_shapes` as well, 0 on all five.

Failed experiments that would otherwise be repeated:

Narrowing the parameters without filling the gaps m2c left is worse (the stack offsets are wrong). Writing the line as
`(width + 7) >> 3` keeps every instruction but the frame. A `do { } while (0)` around part of the command run leaves
the macro rewrite with nothing to write; it has to be opened first. Under `-O2` the finished source scores 13932 to
22642, no better than the saved bests: the level is what matches, so it has to be tried with the source.

What is controlled evidence and what is one function:

Judge measurements on five functions of one game, one compiler build.
