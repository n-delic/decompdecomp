---
title: ternary minmax orientation and signed field read
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32 (-fno-cse-skip-blocks as the project passes it)
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 653-instruction N64 display-list builder (a texture block upload, then a rows x columns loop that writes a clipped
texture-rectangle packet per cell, one version for each screen mode). Saved best 12812 (an m2c draft run through the
search), rewritten from the target's assembly with the SDK's texture-block and scissored texture-rectangle macros on a
global cursor. Two things that no spelling of the saved best could reach:

1. The load-block packet clamps its texel count: the target is `slti v0,v1,0x800; beql v0,zero; addiu v1,zero,0x7ff` and
   then one `andi 0xfff; sll 12` (the clamp is a statement-level branch whose result feeds the shift and mask). Written
   `MIN(lrs, 2047)` as `(lrs > 2047) ? 2047 : lrs` it compiled to a `lui 0x77f; ori 0xf000` constant arm and a duplicated
   `andi/sll` arm.
2. The loop bounds are 4-bit fields of one packed word (`lw; srl 12; andi 0xf` then a signed `slt` against the 16-bit
   counter). The word as `u32` gave `sltu`; as `s32` it gave `sra` and `slt`.

The source change, and the measured result before and after (scores, the judge used):

1. fold-const.c turns a conditional into MIN_EXPR/MAX_EXPR only for `A op B ? A : B` (and `A op C1 ? A : C2` for constants
   one apart). `A > B ? B : A` stays a COND_EXPR, and `fold` then distributes every operator applied to it over both
   arms (`(c ? a : b) & m` -> `c ? a & m : b & m`). Writing the clamp `(a < b) ? a : b` folds to MIN_EXPR, which expands to
   the compare-and-branch the target has. 8442 -> 6262 with that edit alone (the app's judge via the isolated worker).
2. A cast on the extracted value, `(s32) ((word >> 12) & 0xF)`, is the type of a bit-field read (int, logical shift,
   signed compare): 6262 -> 5667; the same bytes as a union holding a real `unsigned rows:4; unsigned cols:4` view.
   Casting alone from the 8442 source: 7847.

decomp-repair `ternary_min_max_orientation` and `field_extract_signed_cast` (PR 1442) write both, and chained on the
pre-edit source they reproduce 8442 -> 6262 -> 5667.

Failed experiments that would otherwise be repeated:

- The scissored rectangle's `(c ? A : B) | T` is distributed by fold when `T` is not itself a conditional: the target
  keeps the join before the OR in both screen modes, so the second mode's T term must be a conditional that only RTL
  can resolve (`(u16) y * 4 < 0` built it; a plain `T` or a `u32` temp for the S word each cost 1000-2000). That is
  scaffolding, not the original source, and no faithful form of it has been found.
- loop.c hoisted the packet opcodes (`lui 0xe100/0xe400/0xf100`) out of the inner loop in every spelling tried, where the
  target hoists only `0xf100`: the move test is `threshold * savings * lifetime >= insn_count` (the threshold falls by 3
  per moved insn; `savings` counts matching loads in both arms), not read to a cause here.

What is controlled evidence and what is one function:

One function. The MIN/MAX orientation is read in fold-const.c and measured here; the field-read type is measured against
a real bit-field view. The loop-hoist threshold is a reading of loop.c's source, not a tested rule.
