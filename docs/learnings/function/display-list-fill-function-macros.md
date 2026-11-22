---
title: display list fill function macros
kind: function
compiler: KMC GCC 2.7.2, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

An N64 display-list function (343 instructions, KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2) that clears the
Z buffer and the frame buffer with fill rectangles: SetDepthImage, SetOtherMode (cycle type, render mode),
SetColorImage, SetFillColor, FillRectangle, then a colour picked from the object's flags (RGBA8888 or RGBA5551) for a
second fill. Saved best 2480 (m2c's raw `*(s32 *)((char *)p + K)` stores, a `volatile` cursor, `__repair_*` copies).

Edit list (app's judge on the work-in-progress source, each row measured against the one before):

| # | edit | score |
|---|---|---|
| 1 | rewritten with the SDK's macros on a plain `Gfx *` cursor (`PKT` = `sDisplayListCursor++`), a union `Gfx` | 11081 |
| 2 | fill rectangle words in locals at the first rectangle (reused by the border-skip return) | 6685 |
| 3 | the fill command word `0xF7000000` in a local assigned just before its first use (a saved register, as the target) | 4185 |
| 4 | the colour image size as `sz = 2; if (mode) sz = ...`, `c = 0` before the flag test, `color = 0` inside it | 3450 -> 2805 |
| 5 | one pointer `gp` for the two fill words, the two cursor advances after the stores (order searched) | 1195 |
| 6 | the mode word's read as a one-member record (`D_8010A4EC.value`), the same for the border flag | 1195 -> 390 -> 255 |
| 7 | the colour image word as `0xFF000000 \| (sz << 0x13) \| ((w - 1) & 0xFFF)` (five shapes searched) | 255 -> 10 |
| 8 | the RGBA5551 channels in `u8` locals, the red-green part in its own local, `c = blue \| rg` (one local per arm) | 10 -> 0 |

Compiler facts (sched.c, `true_dependence` / `output_dependence`; read in the installed source, checked on the output):
- A store through a union member is "in struct" and never conflicts with a plain scalar global's read, so sched1 hoists
  the read of a plain global above every display-list store. A target that keeps the read after the stores read a
  member (a one-member record or an array element). This was the step from 1195 to 255 and cannot be reached by any
  reordering of the source.
- The two cursor advances sit after the data stores in the target; with the macros in order they sit first. Their
  position follows the source order, and one advance written twice in a row is merged by cse, so a data store has to
  separate them.
- cse/combine reassociate `a | (b | K)` when K is a constant: only the spelling `K | a | b` produced the target's
  `((w-1)&0xFFF | 0xFF000000) | (sz << 19)` evaluation order.
- A pseudo live from the function's start takes a saved register: the fill-command local assigned at its first use
  (not at the top) is what gave it `$s4` after the four coordinates' priorities (`passes.py` shows the table).

Scaffolding the match relies on: `fillCmd`, a local holding `0xF7000000` that is used by the first fill and by the
border-skip fill but not by the two later fills (the constant is a pseudo only there in the target); and the
`D_8010A4EC` / `gBorderSkipActive` reads as one-member records (equivalent to the plain reads). Everything else is the
SDK's macros spelled out.

Failed experiments: a ternary for the image size (4185 stayed), the RGBA5551 macro with the blue term first (loads
reordered, 2625), `volatile` on nothing, a `static` temp for the red-green part shared between arms (70-140).

Controlled evidence: the member-record step (reproduced by decomp-repair `member_alias_pair` from the macro source,
1195 -> 390 -> 255). Everything else is one function.
