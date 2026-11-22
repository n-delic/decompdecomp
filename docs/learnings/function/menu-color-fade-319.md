---
title: menu color fade 319
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 319-instruction N64 menu function (u8 clamp, two color-word stores through entry pointers, a float-to-unsigned
conversion stored to a byte, a second one added to a halfword). Saved best 1670 under the app's judge, 0 after the
edit list below. KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`.

Edit list (score after each, in order, on the app's judge):

1. Symbol arm: `&D_800BD660 - 0x14` -> the real symbol of the other arm (the first arm used the wrong one): 1670 -> 1370.
   An address that is not the right symbol costs 150 per row in scoring version 2.
2. Clamp `if ((v + 1) < (0xFFU + 1))` (v a `u8`) -> `if (v < 0xFF)`: 1370 -> 1135. Library: `plus_one_clamp_compare`.
3. m2c's `colorMask` constant reused for a later load: split the load into its own local, and kill the shared
   `-0x100` constant with a dead `colorMask = 0;` before it: 1135 -> 1120. **Scaffolding**: the dead store only
   ends gcc's cse equivalence (cse.c `cse_end_of_basic_block` follows the clamp's branch-around, so the arm's `li`
   was reused after the clamp; the target has a fresh `li` there). Not wired as a family: a phantom store.
4. `temp_a0 = temp_v1_2;` right after the null check, store through `temp_a0` after the float-conversion diamond:
   1120 -> 305 (the target's `move $a0, $v1` in the `bgez` delay slot). Library: `copy_across_join`. Hypothesis:
   a copy is folded into its source inside one cse extended block, a label with two uses ends the block.
5. `*(s16 *)p = (s16)(var_v1 + lhu)` -> `s32 sum = var_v1 + lhu; *(s16 *)p = (s16) sum`: 305 -> 0 (`addu $v0,$v1,$v0`
   operand order). Library: `wide_sum_temp`. Hypothesis: the front end narrows the sum to HImode and `expand_binop`
   puts the REG operand first when the other is a SUBREG; a full-width sum keeps source order.

Chained on the pre-fix source with the app's judge: 1355 -> 1120 -> 305 -> 0 (`plus_one_clamp_compare`,
`copy_across_join`, `wide_sum_temp`; decomp-repair 0.5.630).

Failed experiments: ternary clamp `(v < 0xFF) ? v : 0xFF` (the load went straight into `$s1`, 2360); `s16`/`u16`
for the sum variable (the `| 0x80000000` is dropped as dead in HImode, 820); `(s32)&D_80000001` for the `0x80000000`
constant (the matched sibling uses plain `(u32)f + 1`, the displayed `%hi(D_80000001)` rows are a splat annotation,
the judge compares linked bytes); the clamp written `s1 = 0xFF; if (...) s1 = v;` (same RTL, no change).

Controlled evidence: the three-family chain on this function. One function: everything else, and the dead-store
scaffolding in step 3.
