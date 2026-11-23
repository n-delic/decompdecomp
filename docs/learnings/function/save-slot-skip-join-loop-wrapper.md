---
title: save slot skip join loop wrapper
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function looked like: a 143-instruction N64 save-slot routine (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`),
three loops over a global slot count: a scan for the slot of one type with an inner `goto` loop, a scan of an object
table for the first object in a state, and a loop that sets every object but the chosen one to one of two states. The
saved best (265) was a search's output: an identity pointer rebase through two inline helpers around a loop bound's
address, a `loop_goto_N` label scaffold in the first loop, an identical-arm guard `if (table) { x = table[i]; } else
{ x = table[i]; }` in the third loop.

Edit list (the app's judge, one function, so observation):
1. `rebase(&gTotal, base())` (`base()` returns `&gScene`, `rebase(a, b)` is `(u8 *) &gScene + (a - (u8 *) b)`) written as
   `&gTotal`: 265 -> 205. The first pass made a second pseudo holding `&gScene`; its copy was hoisted out of the loop and
   stayed as a dead `move $a3,$a1` in the register the loop bound's address then took. Library `rebase_identity_strip`
   (`scaffold_strip` declines a function with a `goto`).
2. The existing `loop_goto_form` (the first loop's `loop_goto_N` label as a `do { } while (0)` form): 205 -> 145.
3. The third loop's skipped body and its step: `if (i != chosen) { BODY } i++;` as `if (i == chosen) goto skip; { BODY }
   do { skip: i++; } while (0);` together with the identical-arm guard removed: 145 -> 60. Alone the wrapper left the guard
   at 205 and the guard's removal alone 300. Library `skip_join_loop_wrapper` / `skip_join_collapse`.
4. The loop condition `(s16) i < *(volatile s32 *) &gTotal` as `*(volatile s32 *) &gTotal > (s16) i`: 60 -> 0. Library
   `volatile_compare_sides`.
From the saved best, `prove_chain.py` with the existing `loop_goto_form` and the three new families reaches 0
(skip_join_collapse 180, volatile_compare_sides 120, rebase_identity_strip 70, loop_goto_form 0).

Why (reorg.c read; the effect is measured here, the generality is a hypothesis): `fill_eager_delay_slots` asks
`mostly_true_jump` for each conditional branch. The loop's skip branch is an `EQ` test, predicted not taken, so the
fall-through thread is tried first and its first insn (`sll $v0,$v0,2` of the next table index, dead at the branch's
target) fills the slot: `beq ...; sll`. The target had `beql ...; addiu $v0,$a0,1`, the step copied from the join
label with an annulling branch. A branch to a label directly after a `NOTE_INSN_LOOP_BEG` note is "the top of a loop" and
predicted taken (2): the target thread is tried first. A `do { skip: i++; } while (0);` around the step puts the join
label there. The identical-arm guard had worked for the same reason by accident (a jump in the fall-through ends the
search), at the cost of a real `beq` and different register priorities. `do { } while (0)` alone after the `if` (no
label) and `for`/`continue` forms did not do it (300-360), nor did a named label with an `if (0) goto` (the label is
dropped).

Failed: the helpers' arithmetic in other spellings; guards on other variables (`if (i)`, `if (chosen)`: the arms merge
and vanish with their label); `i = i + 1`, `++i` in the condition.

Controlled evidence and one function: the prediction rule is the compiler source; each step's score is one function.
The scaffolding the match relies on (the wrapper loop and label, the `goto`) is the library family's output, not
something a developer would plausibly write; the rest of the saved best's scaffolding stayed (see the PR).
