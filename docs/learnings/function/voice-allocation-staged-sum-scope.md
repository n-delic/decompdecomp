---
title: A voice allocator's register rows closed by loop-depth weights: a guarded copy block, and a quotient with a staged sum and its test in one scope
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

twine `func_8008D138` (search: func_8008D138, sentinel_local_repairs), a 217-instruction N64 voice allocator in a
`goto` loop over 12-byte records (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, the project profile). An earlier
run took it from 2424 to 85 with -1 sentinel locals (`neg1`/`neg2`, spilled pseudos the reload rematerialises as
`addiu $t1,-1`, as the target does), several `do { } while (0)` weight wrappers, a `u8` quotient temp and `s32 temp_cnt`.
The last 17 rows were registers only: (1) the a5 mask (`arg5 & 0xFF`) and the voice index swapped `$s2`/`$s3`;
(2) the pan sum `((arg0 >> 8) & 0xFF) + p->pan` came out `andi $v0; addu $a0,$a0,$v0` where the target has
`andi $a1,$v0; addu $a1,$a1,$a0`, and the volume quotient's registers depended on it.

The source change, and the measured result before and after (scores, the judge used):

All scores from the app's judge-worker (`fast_judge.py`); the final source was exact in the app (`compile_and_diff`).

| edit | score |
|---|---|
| saved best | 85 |
| 1. `if (temp_v0_2 != neg1) { do { two stores; var_s2 = temp_v0_2; } while (0); }` | 25 |
| 2. pan sum `v = (arg0 >> 8) & 0xFF; v += p->pan;` (alone) | 970 |
| 2 + 3. quotient and sum in one `do { } while (0)`, the factor `p[5]` read into a local before it | 1992 / 570 |
| 2 + 3 + 4. also `do { var_v0_2 = temp_fp + p[4]; } while (0);` | 570 |
| 2 + 3 + 4 + 5. the test inside the scope: `in_range = v < 0x100; } while (0); if (in_range)` | **0** |
| 2 + 3 + 5 without 4 | 1657 |

Why, read from `-dl -dg -dS -dR` dumps and the 2.7.2 source:
- Row 1: global.c ranks `floor_log2(refs) * refs / live`; the mask had 13 references (its uses sit in the doubly
  wrapped call condition) against the voice index's 11. The wrapper adds three to the index (14 / 153 = 0.275 over
  13 / 146 = 0.267). Removing a wrapper from the mask instead dropped the `arg6` mask below the counter (90).
- Row 2: the target's `andi` is not tied to the `srl` (it writes the sum's own pseudo, global because the clamp
  reads it in another block: combine_regs refuses a set register without a local quantity), and the sum prefers the
  mask's register through global.c's `set_preference` (first operand of the plus). Then local-alloc must give the
  shift `$v0` and the product `$v1`: `qty_compare_1` ranks the product (8 references over ~22) above the two-insn
  shift (2 over 2) unless both are doubled by a scope (16 over 22 against 4 over 2). Reading `p[5]` outside keeps
  the walking pointer at 16 references (global priority 0.447 below `var_v0_2`); the scope's extra `arg0` reference
  lifted `arg0` above `temp_fp` into `$fp`, which edit 4 compensates (temp_fp 5 references).
- Row 2's last `nop`: sched.c makes the insn after a loop note depend on everything before it, so with the scope
  closing before the clamp's `slt`, sched2 pinned it last, behind `mfhi`; gas then needs no pad after `mfhi`, one
  instruction short. With the comparison stored inside the scope, sched2 put `slt` before `mfhi` and gas padded
  the `mfhi` hazard with the target's `nop`.

decomp-repair `staged_sum_scope` (families `guarded_body_scope`, `staged_sum_scope`, 0.5.626) proposes both: from
the saved best the greedy chain is guarded (25) then staged with the earlier load-and-add wrapped (0).

Failed experiments that would otherwise be repeated:

- A real `for` loop instead of m2c's goto loop: 9463 (loop.c hoists invariants the target recomputes; the target
  was built from a goto/non-loop form, and its -1s are spilled pseudos).
- Spellings of the sum alone (`x + L`, `L + x`, `(u8)(arg0 >> 8)`, statement order before or after the quotient):
  880-970, never the target's tying.
- Wrapping the call that assigns the voice index: 2400+. Wrapping the else stores twice: 1418 (the -1 sentinel
  outranked `temp_fp` and took `$fp`).
- The scope around the shift alone, or with the clamp `if` inside it: 1075 / 620 (block order of the clamp arms
  changes when its branch sits inside the scope).

What is controlled evidence and what is one function:

One function. The sched.c loop-note dependency is in the source; the allocation readings are from this function's
dumps. Scaffolding the match keeps: `neg1`/`neg2`, the earlier `do { } while (0)` wrappers, `u8 temp_a3`,
`s32 temp_cnt`, and the new `factor`/`in_range` locals and scopes.
