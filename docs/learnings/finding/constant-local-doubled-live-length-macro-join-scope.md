---
title: A constant local's live length is doubled; weight it with a one-shot scope at a macro's internal join
kind: finding
compiler: sn-n64-281 (GCC 2.8.1) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 638-instruction N64 texture loader: five arms on a texture type, each a libultra-style `gDPLoadTextureBlock` written as
brace macros (load tile, `LoadBlock` with `MIN(lrs, 2047)` and `CALC_DXT`, render tile, tile size), a `LoadTLUT` in the
palette arms and one call. Locals `cm = 2; mask = 0;` are passed for cms/cmt and masks/maskt. Saved best 75: the only
rows were the mask and the type exchanged between `$s5` and `$s6`.

cc1n64 `-dl` (run under wibo): type 5 refs over 466 insns (priority `floor_log2(5)*5/466*10000` = 214), mask 6 refs over
1088 (110). The mask's live length is twice the width's (553) although both live to the last arm: GCC 2.8.1
`local-alloc.c` `update_equiv_regs` doubles `REG_LIVE_LENGTH` of a pseudo set once with a REG_EQUIV constant (cse puts a
REG_EQUAL note on `mask = 0`). So the mask needs 8+ references (220) to outrank the type, and nothing else may cross.

The source change, and the measured result before and after (scores, the judge used):

Split the load macro after `LoadBlock` (the `MIN`/`CALC_DXT` branch already ends a basic block there), and wrap the palette
arm's tail (`PipeSync`, render `SetTile`, `SetTileSize`, `LoadTLUT`, the call) in `do { } while (0);`, plus the enclosing
`if (pal != NULL) { ... } else { ... }` in another. Mask 9 refs (248) over the type; the palette pointer gains as much as
the image pointer, so their order (pal `$s2`, img `$s3`) holds. 75 -> 0 in the app (`compile_and_diff`), the app's
judge-worker agreeing. decomp-repair `macro_join_tail_scope` offers it as the 4th of 20 children of the saved best.

Failed experiments that would otherwise be repeated:

- A second `mask = 0;` in the same block is deleted before flow (no change); one in an arm after a join keeps two sets
  (no doubling, 7/547 = 256) but emits a move (1595).
- `do { } while (0)` in the middle of an arm (around one `SetTile`, around `LoadTLUT`, around `mask = 0;`): 560 to 4290;
  it is a scheduling boundary and the target interleaves the arm's stores.
- Whole arm bodies wrapped: arms 8-pal and 8-nopal together 70 (mask fixed, but the image pointer then outranks the
  palette pointer: img 9/481 = 561 against pal 5/246 = 406).

What is controlled evidence and what is one function:

The doubling and the priority formula are read from the GCC 2.8.1 source (`local-alloc.c`, `global.c`); the priorities
agree with the `-dl` table to the digit. That the wrapper is free only where a block boundary already exists is measured
on this one function.
