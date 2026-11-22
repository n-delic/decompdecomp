---
title: goto loop exit delay slots
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-22
---

What the function looked like: a 138-instruction two-arm table search (KMC GCC 2.7.2, N64). Each arm counts a bit
length with a `do { next = i + 1; if (!(q >> next)) break; i = next; } while (i < 11);`, then walks a halfword table
down with m2c's goto loop `loop: if (*(p--) >= t) { idx -= 1; goto loop; }`. Every instruction agreed with the target;
the diff was registers and two delay slots: the do-while's back branch was `bnel` (annulled, the loop head copied into
the slot) where the game had a plain `bnez`, and the walk's exit branch took an unrelated insn of the exit block in its
slot where the game had the pointer step.

The rule (read in reorg.c and traced with a debugger on the app's own cc1: `mark_target_live_regs`, `update_block`):

1. The step `p--` in the condition is an insn before the exit branch. The first delay-slot pass moves it into the
   slot from above and leaves a `(use (insn))` marker where it was. When the second pass decides an EARLIER loop's
   back branch (the do-while: its slot insn is the copy of `next = i + 1`), the liveness scan of that branch's
   fall-through runs onto the marker and counts the pointer's register as live. Pointer and `next` shared `$v1`, so the
   copy needed an annulling `bnel`. With `p--` as the first statement of the loop body there is no marker on that line
   and the back branch is a plain `bnez`.
2. `mostly_true_jump` predicts a jump that leaves a loop (`LABEL_OUTSIDE_LOOP_P`, set in loop.c from the
   LOOP_BEG/LOOP_END notes) as not taken, so `fill_eager_delay_slots` tries the fall-through thread (the step) first.
   A goto loop has no loop notes, the exit predicts taken, and the exit block's first independent insn is pulled into
   the slot. `do { loop: if (c) { ...; goto loop; } } while (0);` gives it the notes and changes nothing else.

The source change and measurements (the app's judge): saved best 150 (an identical-arm guard `if (x || arg0) {A} else {A}`
inside the loop, byte-index typo in one arm); with the guard gone 325, one variable reused for the second arm's quotient
525, the step in the body 245, the arm's table address written `idx * 2` and no wrapper call 230, each goto loop in
`do { } while (0)` 110, the shared `base` pointer split per arm 0. The step alone 245 and the wrapper alone 230 do not
help each other until both are there. `goto_loop_exit_scope` (decomp-repair) offers both as one child: 510 -> 110, then
`arm_private_rename` -> 0; on the saved best itself 150 -> 125 -> (`normalized_phase_chain`) 70 -> (`dead_local_stage`) 40.

Failed experiments: real `while` loops (loop.c rotates them: 1540-1600), statement order of the loop-entry statements
(grid, 24 orders, no change), operand order or locals in the 0x50 store sum (no effect on its register), wrapping
arg0's stores in `do { } while (0)` to raise the register priority (no effect).

Controlled evidence: the lldb trace and reorg.c reading are one compiler build; the score steps are one function.
The prediction rule (item 2) is the compiler source; the exact marker-to-register path (item 1) was seen as the
pointer's register in the live set of the traced fall-through.
