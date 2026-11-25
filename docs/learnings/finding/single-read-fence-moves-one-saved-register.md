---
title: single read fence moves one saved register
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: verified
date: 2026-11-25
---

Two sibling N64 display-list builders (421 and 437 instructions, one 16-bit and one 32-bit texture loop each, SN GCC
2.8.1 `-G0 -mips3 -O2`) sat at 80 and 70 with only the saved registers rotated: the texture pointer (`data`), the second
scale factor (`sy`, a copy of the first constant) and the first one (`sx`) held the target's three registers in the
wrong order, and the scale factors' roles were crossed (the `mult` used the copy, the `div` the original).

Source changes and measured scores (the app's isolated judge, then `compile_and_diff`, both exact in the end):

- `sx = 1; sy = 1;` in that order (the first constant set is the original register, the second its `move` copy, so the
  `mult` uses the original and the `div` the copy as in the target): 80 -> 110 on one sibling (the 80 had the roles
  crossed; the register rows were the same three), 70 unchanged on the other.
- `call_weight_ladder` (two nested `do { } while (0)` round the first four display-list statements of the 16-bit loop):
  110 -> 20 and 70 -> 20. The ladder also adds weight to the temporaries of the fourth statement (a clamp, a mask, a
  shift and a `lui` swap `$v0`/`$v1`), which is the 4 rows left.
- The texture pointer's own read in the first statement only, through a scratch local:
  `{ u8 *p; do { p = data; } while (0); GFX(0xFD100000, (u32)(p + off)); }`: **0 on both** (recorded matches). Wrapping that
  statement alone (`do { do { GFX(...) } while (0); } while (0);`) was 1530 to 1640; the fence has to be on the pointer's
  read, not on the whole statement. decomp-repair `local_read_fence` offers exactly this child (library PR 1723).

Failed experiments: every order and grouping of `0x07000000 | X | Y` in the fourth statement, the clamp as a local with
`if`/ternary/`u32`, the three operands in locals (325 to 5880), `data` assigned later, first or inside the cases (490 to
795), `data` as `u32` or `&img->data[0]`, a data walk instead of an offset (810), declaration orders (no change).

Hypothesis (not confirmed in the compiler source): a `do { } while (0)` is one loop more for flow.c's reference count, so
the fence adds one weighted reference to `data` alone and global.c's `floor_log2(refs) * refs / live length` order of
the saved registers then matches. Controlled evidence: two functions of one shape; the scaffolding is for the match only.
