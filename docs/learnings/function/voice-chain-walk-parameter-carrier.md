---
title: voice chain walk parameter carrier
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A 133-instruction N64 voice-chain walk (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, the project's own profile): look a
node up by an id parameter in a sorted list, take its head id, then loop over the chain (table offset `(id & 0xFF) * 0x1A0`,
three calls, a free-list update). Saved best 45 (only registers differed, in the lookup); the saved best was built from the
permuter's own union scaffolding, which hid the shape. Written naturally it was 3062, and every row was a register or the
layout of the lookup, not the code.

The source change, and the measured result before and after (scores, the judge used `decomp judge-worker`):

1. `D_sym[idx << 2] = h;` instead of `*(sym + idx * 4) = h;` (3062 -> 1925): the product made loop.c hoist the symbol's
   address into a saved register for the whole loop. Family `index_shift_spelling` (hypothesis: a MULT is a candidate
   induction expression in loop.c, a shift is not; not read in loop.c).
2. The lookup's `found = NULL` moved after the `id >= key` compare (1705 -> 1330, `assignment_guard_sink`): before it the
   pointer was live across the compare's `$v0` temporary and took a higher register; after it it shares `$v0`.
3. The no-head default written as the `else` arm the `id == -1` skip jumps into (`goto` into the else block), so the skip
   and the not-found path share the block (1330 -> 905, `default_arm_goto`): `beql` with the block's first instruction in the
   delay slot, as the target has it.
4. The dead parameter as the loop's carrier (`id = head;` in place of a `head` local) with the body's mid-block reload
   (`id = *(u8 *)(rec + 0x63)`) in a local of its own: 905 -> 145 (carrier alone, only registers differ) -> 0
   (`param_loop_carrier`, one child; `redefined_web_split` is the split alone). global.c's `prune_preferences`: a
   conflicting lower-priority pseudo that prefers a register keeps higher-priority ones off it. The parameter prefers `$a0`;
   with it as the carrier the table pointer (which conflicts with it) moved to `$a1`, the carrier to `$a2` and the `-1` to
   `$a1`. Split, the pointer no longer conflicts with the carrier and takes `$a0`. Read in the 2.7.2 source.

Chain proved with the library families from the natural 1705 state: 1705 -> 1330 -> 905 -> 0, each step the best child.

Failed experiments that would otherwise be repeated:

* `register`, a separate head local with its own name, merging any pair of locals (49 renames tried), and moving the early
  `-1` assignment (it is dead and flow deletes it) did nothing for the parameter's register.
* A natural single-expression head (`found && id != -1 ? ... : -1`) emits the `sltu`/`nor` chain and scores worse.
* Merging the loop variable into the parameter without the split is the 145 state; it needs both.

What is controlled evidence and what is one function:

All of it is one function. The compiler rules (global.c preference pruning, loop.c) are read or hypothesised as labelled;
the exact judge decided each score. Reproduced by `tests/test_param_loop_carrier.py` in decomp-repair and
`tests/test_library_family_wiring.py::test_the_voice_chain_walk_steps_are_proposed_and_known_to_agents` here.
