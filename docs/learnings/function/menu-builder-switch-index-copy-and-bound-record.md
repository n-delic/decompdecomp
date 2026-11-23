---
title: A menu builder matched (255 -> 0) with an s32 copy of the switch counter, a float sum local and a record-read loop bound
kind: function
compiler: GCC 2.7.2 (KMC), -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

The 534-instruction N64 menu builder from docs/learnings/function/menu-builder-loop-notes-and-pseudo-numbering.md
(KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`). It has an outer loop over players and an inner
`for (j = 0; j < 3; j++)` over `s16 j` with a three-arm `switch (j)` of text-setup calls, then a loop over the other
players whose bound is a scalar global. Two hand-offs left it at 255: the switch index copy (`move $v1,$s0`), the
third arm's `$f0`/`$f2` pair, and one hoisted `la` scheduled early.

The source change, and the measured result before and after (scores, the judge used; `fast_judge`, the app's own judge):

1. **An `s32` copy of the counter at the top of the loop body, every use in the body renamed: 255 -> 190 -> 140.**
   The copy is `jj = j;`, used in `SLOT(jj)`, `jj == 1` and `switch (jj)`. An `s16` counter's switch index is a
   fresh `sign_extend`, which `expand_end_case` copies. When a loop note (`do { } while (0)`; cse.c
   `cse_end_of_basic_block` stops at NOTE_INSN_LOOP_END before loop.c) ends cse's path before the dispatch, the
   dispatch's extension becomes a cse2 copy of the first one and survives as the move. Every earlier attempt removed
   the note, which lowered loop.c's insn count for the inner loop (147 against 153 real insns). `move_movables`'
   `threshold * savings * lifetime >= insn_count` then hoisted `34.0f` (frame +8, 1427). With the `s32` user copy,
   the dispatch reads one pseudo, so the notes can stay. Wrapping the call statement before the switch
   (`do { SLOT(jj) = f(..); } while (0);`) restores the count (151) and the constant stays inline: 190. Wrapping the
   copy too (`do { jj = j; } while (0);`) adds a loop-depth reference to the counter, which lifts its priority over
   `x`'s sign-extended copy (the 0.27 against 0.28 tie): 140. decomp-repair `switch_index_wide_copy`.
2. **The third arm's float sum in an `f32` local before its wrappers: 140 -> 120.** The local is
   `{ f32 sx = (2.0f * cx - 16.0f) + x; do { do { initTextBuffer(.., (u16) (u32) sx, ..); } while (0); } while (0); }`.
   flow.c weights references by loop depth. With three wrappers, the temporaries' weighted counts (12 against the
   first arm's 6) cross a power of two in local-alloc's `floor_log2 (refs) * refs / life`, so the difference's
   quantity ranks below the conversion's and the pair swaps. The arm's address arguments still need the wrappers'
   weight to keep `$s7`/`$s6`, which is why the sum moves out and the call stays wrapped. With the operands as
   written: 125. With three wrappers: 155. decomp-repair `wrapped_call_float_sum_local`.
3. **The second loop's bound read through a one-member record, with the fence before the loop removed: 120 -> 0.**
   The bound is `typedef struct { s32 n; } ..; for (k = 0; k < D.n; k++)`, and `D[0]` of an array gives 0 as well.
   `true_dependence` lets an array-element store (`MEM_IN_STRUCT_P`, varying address) and a scalar global load pass
   each other, so the store `rows[0][i] = q;` needed an empty `do { } while (0);` to stay above the bound's `lw`
   (docs/learnings/function/menu-builder-loop-notes-and-pseudo-numbering.md, step 2). That fence also kept the
   loop's hoisted base (`la`, cse2-shared with the store's symbol) above it, while the target has the `la` in the
   `lw`'s delay. A record read is `MEM_IN_STRUCT_P` and depends on the store, so the fence goes and the `la` fills
   the delay. decomp-repair `loop_bound_record_read`.

In the library these three steps chain from the saved best to 0, taking the best child of each family in turn.

Failed experiments that would otherwise be repeated:

- Without the `s32` copy, every placement of a loop note between the loop top and the dispatch brings the move back.
  That covers a fence after `SLOT(j) = NULL`, before the call, after the call's store and before the switch, as
  well as a `Label **` slot pointer. Fences inside the arms raise the insn count but break the arms' constant
  addresses (1995-7600).
- Spellings that might add insns without changing the output all compiled to the same insn count: narrow casts on
  the guard global, `(s32) j`, `NULL` against `0`, `default:`, `continue` arms and `u16` argument locals. The `&&`
  guard adds 2 insns but changes the code.
- For the `la`: a fence before the store, a wrapped store, a pointer-arithmetic store (600: the store then floats
  above the earlier halfword stores), a row-pointer local, `if (n > 0)` around the loop and a do-while form were all
  120 or worse.

What is controlled evidence and what is one function:

One function (observation). The loop.c threshold arithmetic and the cse path rule are read from the 2.7.2 source and
checked against this function's `-dL` and `.cse2` dumps (147 real insns hoisted, 151 or more kept the constant inline).
