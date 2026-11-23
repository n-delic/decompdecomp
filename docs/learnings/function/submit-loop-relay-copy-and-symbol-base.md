---
title: submit loop relay copy and symbol base
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

twine `func_80007958`, a 511-instruction N64 display-list submit function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, the project
profile): a per-view loop over six table entries, then a buffer swap. The saved best was at 85: two reordered rows in the loop
preheader and five register rows in the loop's first block. Both were closed, 85 -> 25 -> 0, by two edits that no family produced
(`prove_chain.py` over every family from the saved best found no child); each is now a decomp-repair family.

**1. The preheader order (85 -> 25): `symbol_offset_base_inline`.** m2c wrote the three flag bytes the loop tests as one pointer local
(`modeFlags = ((u8 *) renderMode) - 0x21;` before the loop, then `modeFlags[0]`, `[-1]`, `[-2]`). The target's `addiu $s5, $v1, -0x21`
sits after the loop's float constants and before `addiu $fp, 0xff`; ours sat first. Reading each byte as the symbol,
`((u8 *) &D_8010A4E4)[-0x21]` (`[-0x22]`, `[-0x23]`), moved it into place with the same instruction count: all 24 orders of the four
statements before the loop had left it first, and assigning the pointer inside the loop made cse fold the add into each read's
offset (`lbu -0x21($s5)` with `$s5` the symbol itself: 740). Hypothesis, not confirmed stage by stage: cse's related-value logic
turns the first `SYM - K` into one `addiu` from the symbol register and the rest into offsets from it, and loop.c hoists that add
as a movable in order of first use; a user pointer local is set before the loop and so sits first.

**2. The first block's registers (25 -> 0): `pointer_relay_copy`.** The target loads the table entry into `$v0`, tests it (`beql $v0`), reads
`0x12c($v0)`, and puts one `move $a0, $v0` in the delay slot of the float conversion's `bc1t`; the alpha store (`sw $v1, 0x134($a0)`) sits in
the call's delay slot and shares `$a0` with the call. Ours had the entry in `$a0` and the store through a copy in `$a1` (the call took the
entry). global.c: `expand_preferences` walks the insns in order and, at each reg-reg copy whose source dies, merges the two pseudos'
register preferences; `set_preference` has already given the call argument `$a0`. With one copy before the conversion the argument
pseudo is the copy's destination, the merge hands `$a0` to the entry and `find_reg` coalesces them (no move). With a chain
`Q = entry` before the conversion and `view = Q` after it, the first merge sees `Q` with no preference, so the entry takes the lowest free
register (`$v0`), and only then does `Q` take the argument's `$a0` at the second copy and coalesce with `view`. cse cannot fold the chain: the
copies sit on both sides of the conversion's join label (a new cse block) and combine cannot fold `view = Q` into the call because `view`
is the store's base too. The edit list (each alone is 375 or worse):
1. a local `first` for the entry web (load, null test, opacity read);
2. `p = first;` ahead of the conversion;
3. the conversion into a local, `alpha32 = (u32) (opacity * 255.0f);`;
4. `view = p;` after it, then `view->alpha = alpha32; func_800344DC(view);`: **0**.
Without step 3 (store written first, then the copy) it scores 375: the copy has to land after the conversion's join and before the store.

Failed (repeat none): `inline_fn2345(view)->alpha` with every combination of store and call through the inline's copy or the pointer (25 at
best: the call keeps the entry in `$a0`); `p = inline(first)` before or after the null test, the load or the clamp (435/375: the single copy
coalesces); a second user copy `q = p` or a nested identity inline (cse collapses the chain: one copy); `register`; the opacity read through
the copy (cse substitutes the newest copy, so the entry dies at it); the direct `sTrackMarkerTable[i]` for the store and call (reloads after
the join: cse's table is gone there).

Controlled vs one function: both families were measured on this one function (the exact judge, a standalone worker); the register-preference
reading is from global.c and the allocation dump (`passes.py`, `-dl -dg`) and is a hypothesis the judge confirmed once.
