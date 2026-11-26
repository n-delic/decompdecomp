---
title: folded entry value fence
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (also sn-n64-272-0001 -O1 drafts)
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a table search loop over 8-byte slots whose
entry values m2c wrote as `var_a1 = 0 * 8; var_v1 = 0;` and later `var_a1 = var_v1 * 8;`. The target computes the first
`sll $a1,$v1,3` from the live zero in `$v1` (`move $v1,$zero` first).

The source change, and the measured result before and after (scores, the judge used): `do { var_v1 = 0; } while (0);
var_a1 = var_v1 * 8;`. The fence keeps cse from folding the zero shift. 205 -> 0 (fast_judge). Without the fence
(`var_v1 = 0; var_a1 = var_v1 * 8;`) the shift folds again and the function scores 230. Library family
`folded_entry_fence` (decomp-repair: `folded_entry_fence.py`), for two adjacent statements `A = K1 OP K2;` and `V = K;`
(either order, K equal to one operand), one child per pair. `prove_chain.py --families folded_entry_fence` reaches 0 from
the saved pre-match source in one step. It is the opposite direction of `one_shot_unwrap`.

Failed experiments that would otherwise be repeated: `prove_chain --families all --children 400` from the pre-match
source stayed at 205, and from an older m2c draft only `record_slot_table` improved (570 -> 200).

What is controlled evidence and what is one function: one function; the 230 score for the unfenced spelling is measured.
