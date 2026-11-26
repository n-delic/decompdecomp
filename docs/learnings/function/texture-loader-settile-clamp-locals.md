---
title: Texture loader: SetTile clamp locals set first, then statement order
kind: function
compiler: SN64 GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 606-instruction N64 texture loader: a gDPPipeSync, a switch on the texture format with one gDPLoadTextureBlock-like
sequence per format (CI4 and CI8 with a palette load, RGBA16, RGBA32), then a four-level mip-map loop of SetTile and
SetTileSize, returning through a helper. SN64 GCC 2.8.1 with the project's default flags. The saved best (7532, after
six agents) had the frame wrong (0x50 against the target's 0x58), every saved register one off, and the cursor, two
palette constants and fmt/siz/bpt spilled where the target spills them differently. The target ORs two registers that
hold 0 into the CI4 render tile's word (`move $s5, $zero; ... move $s6, $s5` in a branch's delay slot, then
`or $v0, $s5, $fp; or $v0, $v0, $s6`) and the mip loop holds two more zero registers (`move $t3, $t0; move $t2, $t0`,
copies of the counter's 0).

The source change, and the measured result before and after (scores, the judge used):

Isolated judge (`fast_judge`, the app's scorer), confirmed with the app's `compile_and_diff`:
- the matched sibling loader's spelling (the `G(gfx, a, b)` macro, `CALC_DXT`/`TXL2WORDS`, `x < 0x800 ? x : 0x7FF`
  clamps, `img = (u8 *)entry->tex; ... img += 8`), with cmt/cms locals in only the CI4 case and before the loop: 6717;
- `(width << 1) * height` in the load-block sizes (the target multiplies `w << 1` by `h`; every `* 2` spelling let cse
  share the tile size's `h * 2` instead): 6779, kept for its shape;
- **`cmt = cms = 0;` at the top, `((cmt & 3) << 18)` and `((cms & 3) << 8)` in every SetTile word** (both commands of
  each format and the loop's): 2575. The frame, the zero registers and the spill slots all followed. Locals set in one
  case, before the loop, or both scored 3745 to 10145;
- `siz = 2; fmt = 0; bpt = 4;` in the RGBA16 case (grid): 2415;
- `bpt = 8;` at the end of the RGBA32 case: 1600. Written before the commands, cse put its register (8) into every
  later `gfx + 8`;
- the header order `width, height, ws, npal, ts` (grid of five statements): 670;
- the mip loop's tail `ws--; ts--; width >>= 1; height >>= 1;` before the `if (line == 0) line = 1;` clamp (grid): 0.
  With the clamp first the decrements fell into the next block and the SetTileSize words were computed before them.

No scaffolding (no fences, phantom locals or `volatile`).

Failed experiments that would otherwise be repeated:
- cmt/cms set in the cases that use them (every case): 10361;
- `width * 2 * height`, `2 * width * height`, `width * (height * 2)`, `height * width * 2`: 6717 to 7414, all with the
  shared `h * 2` register;
- six earlier agents tuned the saved best's own spelling (the `CMD` macro with comma expressions, reassociated OR
  chains, fences): 46969 -> 7532, never the frame.

What is controlled evidence and what is one function:

Controlled: removing the clamp fields and the locals from the matched source scores 7042, and decomp-repair
`settile_clamp_locals` (0.6.63) restores them exactly (its first child is 0). The statement orders are this function's
(found by `grid.py`); the bpt slide and the clamp-after-decrements are one function each.
