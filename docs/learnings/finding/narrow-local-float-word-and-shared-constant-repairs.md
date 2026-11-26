---
title: Narrow mask locals, float word loads and shared stored constants
kind: finding
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-26
---

## Findings (three functions of one N64 racing game, each 100 or 15 before, exact after; exact judge, saved matches untouched)

1. A byte stored through a table (about 40 instructions). Pre-match: `s16 t; t = (E >> 0x1F) & 0xFF;` scored 100 (no `andi` emitted: the compiler folds a redundant `&` on a halfword local). Exact (0): `u8 t; t = E;`; `u8 t` with the mask kept was also 0. The local's narrow type carries the zero-extension, which is the target's `andi $a0, $a0, 0xFF`. Family `mask_local_retype` (`narrow_unmask` is the reverse, offered only with the target's `andi` and no mask in the source). `masked_local_narrow` could not offer it: it keeps the mask and takes only word-typed locals.
2. A 3x3 float matrix copy loop. Pre-match: `f32 t = *(s32 *)addr; *dst = t;` scored 100 (`lw` + `cvt.s.w`). Exact (0): `*(f32 *)addr` (`lwc1`/`swc1`). Family `float_word_load_retype`: a word read assigned to an `f32` local, offered when the target has `lwc1` and fewer `cvt.s.w` than the source has such sites. `unknown32_float` only covers `M2C_UNK32`.
3. A 20-instruction reset routine. Pre-match scored 15 with `field = 0;`; the target stores `$a0` (loaded `1`, shared with the earlier `flag = 1;`). `field = 1;` is exact (0). The draft's constant came from an adapted twin. Family `zero_store_shared_constant`: counts the target's stores per constant register against the source's literal stores, and offers a `0` store as the constant the target stores more often than the source does. This changes behaviour: it is only a candidate for the judge, and only with the target's assembly.

Failed experiments: `prove_chain.py --families all` on each pre-match source stayed at 100/100/15 before the families existed.

Controlled evidence: all three are single functions; the families are hypotheses the judge confirms per function.
