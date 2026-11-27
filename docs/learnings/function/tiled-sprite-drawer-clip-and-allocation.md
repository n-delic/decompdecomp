---
title: Tiled sprite drawers with a clipped rectangle are exact under -O1; the far-edge clamp decides the registers
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 drawers of 803 and 843 instructions (project default -O2, saved bests above 70,000): they clip a rectangle
against a near and a far edge, give up if a side went negative, load a palette and draw the image as a grid of texture
tiles with a right column and a bottom row for the remainders. The 8-bit one uses `gDPLoadTLUT_pal256` and
`gDPLoadTextureTile` (8b), its sibling `gDPLoadTLUT_pal16` and the 4b tile load. Both are exact under -O1.

The source change, and the measured result before and after (scores, the judge used), all with the app's judge:

- A rewrite with the SDK macros on a local `Gfx *` (read once, stored back at the end) compiled first at 825.
- The coordinates and sizes are `s16` parameters modified in place; local copies of two of them scored 825 against 350.
- Each early return reads the flag through a byte local (`r = flag; if (r) return;`, the target's `move; bnez`):
  350 -> 125. Library family `guard_flag_copy`.
- The rectangle's far corner as `tileW + x + j * tileW`: 125 -> 85.
- The bottom clamp as `h -= y + h - bottom;` instead of `h = bottom - y;`: 85 (17 register rows) -> 0 on both. Library
  family `clamp_overshoot_subtract`. cc1's `.greg` table showed why a register-only near miss needed it: the flag had a
  higher priority than the length, and each `do { } while (0)` that raised the length moved another register.

Failed experiments that would otherwise be repeated:

About 1,500 pairs of `do { } while (0)` wrappers over the clip statements (best 50), flag and copy types, local copies of
the edges, `register`, declaration moves. The greedy chain with every family found nothing from 85.

What is controlled evidence and what is one function:

Each score is one change on the same source, judged by the app's judge. Two sibling functions are the evidence. Why the
compound spelling counts one more reference after folding is a hypothesis; the compiler source was not read.
