---
title: A menu builder's loop-note wrappers, a narrow sum cast and the pseudo numbering a dead wide local changes (420 -> 245)
kind: function
compiler: GCC 2.7.2 (KMC), -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 535-instruction N64 menu builder (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): an outer loop over players, an inner `for (j = 0; j < 3; j++)` over `s16 j` with a `switch (j)` of three text-setup calls (float argument sums `(2.0f * cx - 16.0f) + x` converted with `(u16)(u32)`), then a loop over the other players. The saved best was 420 with scaffolding (nested `do { } while (0)` wrappers); the target's last rows were three register groups and one order group.

The source change, and the measured result before and after (scores, the judge used; `fast_judge`, the app's own judge):

1. **A narrow cast on the inner sum of a halfword store, 420 -> 330** (`narrow_sum_operand_cast`, decomp-repair PR 1498). `*(u16 *)(q + 0x20) = W + (x + 0xFFC7U);` compiles to `(W + K) + x` whatever the operand order (`x + K + W`, `(x + K) + W` and the `s16`-cast spellings compile identically: fold reassociates the constant out of the inner sum), so the first `addu` takes the global's load; the target's first `addu` holds the variable. `W + (u16) (x + 0xFFC7U)` keeps `(x + K)` a unit. A toy of four lines reproduces it in a second (`compile_snippet.py`). The neighbouring statement with the other constant matched already, so the child is per statement.
2. **A wrapper moved past the next statement, with the loop's redundant entry guard dropped, 330 -> 260** (`wrapper_boundary_shift`, PR 1509). The store `D_8011F2E8[0][i] = q;` before `if (n > 0) { for (...) }` must stay above the load of the bound `n` in the target (`sw; lw; la; blez`). The first scheduler orders the two independent memory references by priority (an array element store is `MEM_IN_STRUCT_P` at a varying address, the bound a scalar at a fixed one: `true_dependence` says no dependence) and the store sank into the branch's delay slot. A loop note between them is a scheduling boundary: taking the wrapper off the previous statement and putting an empty `do { } while (0);` after the store, together with writing `if (n > 0) { for (k = 0; k < n; k++) ... }` as the bare `for`, gave 260. Each part alone: 330 (the guard), 955 (the wrapper off), 330 (the fence).
3. **Pseudo numbering from a dead wide local, 330 -> 245, found by the existing `phantom_wide_local` and `ladder_trim` chain** (not landed in the saved best: it is an unused `long long`). `{ long long phantom_wide = rmw1; }` (or `= x`) in the third arm fixes both the float register swap of that arm and the extra `move $v1,$s0` before the dispatch, and lets the wrapper around `SLOT(j) = NULL` come off. A dead assignment of a `long long` creates two pseudos that flow deletes, so nothing in the output but every later pseudo number moves; an `int`, `f32` or `u32` dead local did nothing, an *used* extra local made it 1000+ worse. So the allocation order is decided by pseudo numbers, not by anything the code says (the next point).

What the compiler does there (read from the source, not hypotheses unless said):

- `local-alloc.c` `block_alloc` sorts a block with exactly three local quantities by hand and compares quantity *numbers* after the first exchange (docs/learnings/finding/local-alloc-three-quantity-block-sort.md); the arm `P = 2*cx; T = P - 16.0f; C = (float) x; S = T + C` is such a block (pseudos 241, 243, 244 against 182, 184, 185 in the arm that matched). Same shape, same ratios of priority, opposite registers: only the numbers differ.
- A `do { } while (0)` around the first statement of the loop body (`SLOT(j) = NULL;`) ends cse's path (NOTE_INSN_LOOP_END) so block 2 computes its own `sext(j)` pseudo; the loop pass makes it `156 = 141` and cse after the loop cannot propagate it into the dispatch, which is a new extended block after the `if (SLOT(j) != NULL)` jump (a conditional jump's fall-through starts a fresh cse table), so the dispatch tests the copy and `move $v1,$s0` stays. Without the wrapper the copy is gone but the loop pass hoists the constant `34.0f` as well as `16.0f` (a frame 8 bytes bigger, 1502). A toy of the loop reproduces both (wrapper: copy; none: no copy).

Failed experiments that would otherwise be repeated:

- Spellings of the dispatch (`switch ((s32) j)`, `jj = j; switch (jj)`, an if-tree instead of the switch, the guard as `&&`): the copy stays or the constants move; 3000-8000 for the trees.
- Wrapper counts on every arm and statement (a few thousand scored by grid and a random climb): nothing below 245 from the phantom, nothing below 260 without it. Counts decide constant hoisting (`34.0f` stays inline only with two wrappers on its arm).
- `x + T` instead of `T + x` fixes the `add.s` operand row (260 -> 255) but not the registers; used locals for the two float arguments: 2400-3700.
- The last rows of the phantom version are `$s3`/`$s4` (the counter `j` against `x`'s sign-extended copy, priorities 0.27 and 0.28) and the order of one `la`; wrapper counts around the arms do not move either.

What is controlled evidence and what is one function:

One function (observation). The narrow cast reproduces on a four-line toy (controlled); the loop-note and numbering explanations are read from the compiler's source and the dumps of this function only.
