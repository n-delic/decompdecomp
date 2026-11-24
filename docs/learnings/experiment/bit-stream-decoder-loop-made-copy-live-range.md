---
title: bit stream decoder loop made copy live range
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 681-instruction N64 bit-stream decompressor of `bit-stream-decoder-histogram-store-and-last-pass-limits.md`, resumed
at its saved 85 (17 register rows, all in the last table pass). The replay (`alloc_roles.py`) needs the inner loop's
loop-made copy of `D_800B3134 + k` (pseudo 328: 11 weighted refs over 15 insns, 2.20) between 1.08 and 1.286, which with
11 references means a live length of 26 to 30 insns: its set would have to sit near the top of the middle loop's body,
not just before the inner loop.

The source change, and the measured result before and after (scores, the judge used):

No improvement (85 stays the best; app's judge-worker, one `compile_and_diff` confirming 85). What the loop dump
(`-dL`) shows about where that copy comes from, which rules out most of the obvious shapes:

- The inner loop moves `k * 4`, `D_800B3134`, their sum (insn "cond forces"), the same for `D_800B283C`, and the
  constant 1 to just before its `NOTE_INSN_LOOP_BEG`; the middle loop then moves the symbols and the constant further out
  and reduces the two sums to new registers, leaving `P = R` copies (non-user pseudos, so no `copy_cost`) at the place the
  inner loop put them. Nothing in the middle loop's processing moves that copy earlier.
- A `do { } while (0)` around the middle body (or its tail with the inner loop) is reported "Loop ... is phony" by
  loop.c: it is never scanned, so the copy is not hoisted to its top (1515, 800).
- A user pointer `vp = D_800B3134 + k` (top of body, after the first load, just before the loop) is replaceable whatever
  its place (record_giv: first set is the giv insn, last use inside the loop, no biv update in its range), so the copy
  disappears (1704 in all three places). An unused user label before it changes nothing (hypothesis: jump.c
  deletes an unused label before loop.c sees it; 1704, identical).
- Making the copy's live range longer by padding the inner loop: user copies (`ny = y + s; y = ny;`, `yy = y`) are
  removed by cse before flow counts lengths (85, identical); identical-arm `if (tb|a|c|mask) {S} else {S}` around the
  step or the store is not merged back (990..2130).
- Spellings of the inner statements: `* 128` and `u16 *d = &sb[y]` compile identically (85); a `u16` value local 2944;
  the two loads in locals 520-680; the operands swapped 130; `s32` shift amount or a staged step identical (85).
- The last pass's `lim` from `c` (`gp + c`, `D_80112EE0 + c`, `H + c`) instead of `off`: 2159-2184.
- Profiles on the 85 source (`fast_judge.py --flags`, added to the function's own): `-G8`, without
  `-fno-schedule-insns`, `-fno-cse-follow-jumps`: 85; `-fno-expensive-optimizations` 390; `-fno-rerun-cse-after-loop`
  2005; `-fno-strength-reduce` 12446; `-O1` 17125; `-funroll-loops` 44495. The function's profile is right.

Failed experiments that would otherwise be repeated:

All of the above. The paths loop.c leaves for a copy set early are: a user giv made non-replaceable by
`not_every_iteration` (its set after a real label or jump in the middle body, its use in another block) or by a back
branch crossing `k++`; both need code the target does not have, so the remaining row group probably needs a different
loop nesting or a different owner of the `$s0` copy, not another spelling of this one.

What is controlled evidence and what is one function:

One function. The loop.c facts (the phony loop, the movable order, the replaceability tests) are read from the
`-dL` dumps and the 2.7.2 source (`record_giv`, `check_final_value`, `back_branch_in_range_p` in unroll.c); the scores are
the app's judge-worker.
