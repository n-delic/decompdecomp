---
title: A 359-instruction voice picker: chain walks as while loops, 4210 to 0
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A voice picker for an audio engine (N64, KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`), 359 instructions, four
parameters (priority, wanted count, key, flag) and a return of the picked slot or -1. It walks a list of priority
positions (16-bit links); at each position it walks a chain of byte links (`next` until 0xFF) over 416-byte voice
records, three times: a first search that counts matching voices and keeps the least-valuable free one, a second pass
that only counts, and a fallback search without the key. A commit then unlinks the pick from a free list (a 4-byte
record: previous, next, 16-bit used flag) or decrements the table count of the voice it steals, and bumps the table
count of its own kind. The saved best was m2c's goto ladder plus search scaffolding (`__repair_*` locals, identity
inlines, a `char` return), judged 4210; the library chain from it reached 4075 and stopped.

The source change, and the measured result before and after (scores, the judge used; `fast_judge`, the app's own
judge, one source per row; "alone" rows are the step applied to the source before it, not to the saved best):

A structured rewrite from the assembly (a clean source built from the target's blocks, not an edit of the best)
scored 9232 and the steps below took it to 0. Each score is the app's judge on the whole function after that step.
1. `s32` for the chain index and the parameters (a `u8` index added an `andi 0xff` at every use), and the product
   `idx * 4` spelled as a variable: 9232 -> 5240.
2. The early `if (D_80112DC0 != 0) return -1;` as `if (D_80112DC0 == 0) { REST } return -1;`: 5240 -> 4855
   (`early_return_guard_block`, new).
3. The byte-offset reads of the chains and the free list as typed records (`SlotLinks D_80102CE0[]` with `.next`,
   `IndexLink D_8010A51A[]` with `.index`, the free list as `SlotLinks`): 4845 -> 4265. Without the typed element the
   loop pass hoists `la sym` out of the walk where the target addresses `%lo(sym)($reg)`. Not a family yet (it needs
   the header's record types).
4. Both arms of the last table update returning the pick, as `if (c) { A } else { B } return best;`: 4265 -> 2640
   (`shared_return_tail`, new).
5. Named `u32` temporaries for the phase-3 head's unsigned compare of the priority against the first link (2640 ->
   2415), then `u8 strict` read as `strict` (never `& 0xFF`): 2415 -> 2310 (`masked_local_narrow` exists).
6. `if (idx != 0xFF) { do { ...; idx = next[idx]; } while (idx != 0xFF); }` as `while (idx != 0xFF) { ... }` in all
   three walks together: 2310 -> 1550 (`guarded_sentinel_while`, new). The guarded do-while shares one 0xFF register
   between the guard and the loop test; the while keeps them apart, as the target does (`addiu $s1,$zero,0xff` before
   the search loop and `addiu $t7,$zero,0xff` in the inner preheader).
7. The masked cursor copy `pos = cur & 0xFFFF` as the first statement of the search loop instead of before it and
   after the cursor's step: 1550 -> 1020 (`loop_top_cursor_copy`, new).
8. The last compare, a masked priority against a stored byte, as an unsigned compare (1020 -> 820,
   `masked_compare_unsigned`, new; on the earlier form it also took the masked head compare, 2640 -> 2245). Then
   statement placement and operand order: `best = -1;` inside the search
   `if` (-> 650), `cur = head;` before the third search's guard and its priority recomputed in the loop (-> 430), the
   third loop's cursor copy reused as its product (a `t1` shared, -> 360), and the third loop's compare in the target's
   load order. No family for these but the next one.
9. The commit's two link bytes written into `idx` (360 -> 330) and the third loop's product into the dead cursor copy
   (455 -> 60 on the source with the compare order of step 8): `dead_scratch_reuse`, new.
10. Two `do { } while (0)` fences (a weight on `strict = 0;` and around the third loop's record test), found by the
    library chain from 60: 60 -> 30 -> 10 (`clamp_default_scope`, `statement_scope_weight`). Scaffolding: they only
    raise two pseudos' loop-depth weight (strict 4 -> 5 references so it ranks over the -1 constant; the loop's
    strict copy over the 0xFF constant).
11. The commit's `if (best == -1) return -1;` as `if (best != -1) { ... }` inside the outer guard block, followed by
    the function's `return -1;`: 10 -> 0 (`early_return_guard_block`). The branch then keeps the target's
    `addiu $v0,$zero,-1` block instead of skipping it (reorg's redundant-insn skip of that branch was the whole
    difference: one branch operand).

Chain evidence: `prove_chain.py` on the 5240 source (step 1) with only the six new families and `masked_local_narrow`
goes 5240 -> 4515 (`guarded_sentinel_while`) -> 3960 (`early_return_guard_block`) -> 2635 (`shared_return_tail`) -> 2215
(`loop_top_cursor_copy`) -> 1820 (`masked_compare_unsigned`) -> 1715 (`early_return_guard_block`, the inner guard) ->
1695 (`dead_scratch_reuse`) and stops there: the typed records (step 3), the statement placements of step 8 and the
last fences are what the library still has to find. The library chain from the saved best (4210) reached 4075 alone.

Failed experiments that would otherwise be repeated:

- A separate local for the third loop's product (`off3`) gave 515: the product is computed into a block-local temporary
  and copied (`move $a1,$v1`) after its first use. Only sharing a dead local gave the target's direct `sll $a1,$v0,5`.
- Reading the comparison of the third loop idx-first (as the first two loops) put the loads in the wrong order and
  3 more saved registers; the target loads the picked voice's value first there.
- `strict` as `s32` with `& 0xFF` at the uses or the cursor as `u16` with `& 0xFFFF` on the copy were both worse than
  `u8` and a plain `u32` copy.

What is controlled evidence and what is one function:

Steps 2, 4, 6, 7, 8 (the compare), 9 and 11 are controlled on this function: each family, run on the source before the step, produces the
child that the exact judge scores as above (reproduced in each library test and PR). Everything else is one function.
Hypotheses, not read in the compiler's source: the guard/loop constant sharing (cse before jump.c's loop-test
duplication), the second `andi` of the cursor copy (reorg fills the loop-back branch's slot with the first insn of the
body), and the redundant-insn skip of the commit's branch.
