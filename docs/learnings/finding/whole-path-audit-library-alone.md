---
title: Whole-path audit: does the library alone reach 0 from a matched function's pre-match best?
kind: finding
compiler: KMC GCC 2.7.2 (-G0 -mips3 -mgp32 -mfp32 -O2)
evidence: verified
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A sample of 42 matched functions (8 to 300 instructions, seeded random) of a KMC N64 game, all judged with their own
profiles (`-G0 -mips3 -mgp32 -mfp32 -O2`, scoring version 2). For each, the start is the lowest-scoring compiled attempt
recorded before its first 0 (read from the app database read-only), re-scored now; functions whose matched source no
longer scores 0 were skipped. `prove_chain.py`'s greedy chain ran from there with every family (`--families all`,
8 steps, 80 children a step, the isolated judge only; no Deep Permuter).

The source change, and the measured result before and after (scores, the judge used):

- **8 of 42 reached 0** with the library alone; 1 more improved without matching; 33 did not move.
- **Why it stalled (34 stalls)**:
  1. **No children at all: 13.** The saved best still defined `func_<VRAM>` (saved before the function got its real
     name), and every family looks the function up by its current name. The permuter's repair round (`permute.py`) passed
     `fn['best_source']` as saved; the Deep Permuter already used `canonical_source`. Fixed: the repair round and
     `prove_chain.py` propose from the source under its current name. Re-run on those 12 (one kept a stall for another
     reason): **3 reached 0** (`global_record_copy`, `offset_base_local`, `float_argument_bits`) and 2 improved
     (`switch_variable_split` + `split_case_labels` 1065 -> 1001; `sum_shape` 2580 -> 2530). Total 11 of 42 at 0.
  2. Remaining stalls by the diff's dominant row kind: register allocation 15, operand 9, reorder 7, extra 2, stack 1.
  3. The hand edits that finished the small stalls (at most 9 changed lines, 15 functions), by missing step:
     - **adjacent independent stores in the other order** (a list-building loop's `D[i].prev = ...; D[i - 1].next = ...;`)
       and a comparison's load order (`a < b` read with `b` first): 2. Fixed the first in decomp-repair #1202
       (`adjacent_array_store_order` now swaps adjacent stores to different fields of one array's elements; 20 -> 0).
     - **a subexpression held in a temporary** (`t = D + 0xFFFC; return (x + t) & 0xFFFF;`, a pre-decremented
       temporary split in two): 3.
     - **reassociation** (`s + (i << 4) + 0x300` -> `s + ((i << 4) + 0x300)`): 1.
     - a block turned into an early `goto`, a parameter copy cleared, a stack struct padded, a typed record view of a
       byte-offset loop, a dropped `__base_` local: 1 each.
     - not a faithful step (a return constant changed): 1, the rest were long rewrites (16+ lines).

Failed experiments that would otherwise be repeated:

- Larger child caps don't explain the stalls: every stall with children scored fewer than 80, so `--children` was
  never the limit.

What is controlled evidence and what is one function:

The 13 no-children stalls and the rename cause are verified per function (the saved best names `func_<VRAM>`); the fix
is covered by tests (`tests/test_draft_repairs.py`, `tests/test_match_skill_prove_chain.py`). The kinds in 2 and 3 are
one game's sample; the temporary-hoist and reassociation steps are the next families to look at.
