---
title: bit stream decoder loop test invariant hoist
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 681-instruction N64 bit-stream decompressor of `bit-stream-decoder-loop-made-copy-live-range.md`, resumed at its
saved 85 (17 register rows in the last table pass). The fill loop's loop-made copy of `D_800B3134 + k` (11 refs over
15 insns, 2.20) outranked the pointers the target gives `$t5`/`$t6`.

The source change, and the measured result before and after (scores, the judge used):

| step | edit | score |
|---|---|---|
| 1 | the last pass's guard `if (H[c] != 0)` instead of a user pointer `q4 = H + c; ... q4++` (the giv init then reads `off + H`, the target's operand order) | 85 -> 85 |
| 2 | the fill loop's shift amount written in its test, `while ((y >> (a = (ent >> 4) & 7)) == 0)`, not set before the loop | 85 -> 70 |

Step 2's rule (loop.c `move_movables`): hoisted invariants are emitted before the loop's start note in the order they
appear in the loop, after every statement written before the loop. An invariant set in the test is hoisted after the
copy of the body's invariant address, so that copy's life grew 15 -> 18 and its priority fell 2.20 -> 1.83 (five rows).
decomp-repair `loop_condition_invariant_set` (#1635) proposes it. Behaviour: 1600 generated streams (stored blocks,
canonical codes to length 15, matches, table reuse) equal against the 85 source; a one-token mutant of the last pass
differed on most of them.

The replay (`alloc_roles.py` priorities, brute-forced over pairs and triples of allocnos) at 70 needs the copy at or
below the `off` biv (1.286, i.e. a life of 26 or more) and the subtable base between 1.81 and 1.875 (a life of 18); no
change of other priorities alone reaches the target.

Failed experiments that would otherwise be repeated:

- The store's value computed first (`v = (sym[k] << 7) + len[k]; sb[y] = v;`, any local): the copy lands early, but the
  table pointer `tab` stops being spilled and the frame shrinks by 8 (2944-2984).
- A user pointer for the symbol entry at the top of the middle body or of the fill loop: the user giv is "not worth
  while" (copy cost) and is recomputed from `k * 4` (375-1704); unused, it is deleted (70).
- The subtable base inline in the store: its hoisted insns come before the copy (address before value), no change.
- Phony `do { } while (0)` around the middle body's statements: they do raise flow.c's loop-depth weights (refs), but
  move the schedule and other roles (345-1490).
- R7's mask as `(1 << tb) - 1` (the target's `sllv ...,$s3`): 155 at 70, `tb` (0.851) passes the R9 `H + off` giv
  (0.818); the giv needs one more depth-3 reference or `tb` three fewer.

What is controlled evidence and what is one function:

One function; the loop.c ordering is read from the `-dL` dump and `move_movables`, the scores are the app's
judge-worker with one `compile_and_diff` recording 70.
