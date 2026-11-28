---
title: A returned display-list cursor a loop appends to, and a loop whose exit test stays at the top
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

An 82-instruction N64 routine takes a display-list pointer, writes four setup commands, adds one fill rectangle for
each on-screen entry of a table, writes a pipe sync and returns the pointer. Judge: the app's, under the profile above.
Symptom: m2c writes the cursor as two pointer chains through the loop (`words = cursor - 4`, both stepped by 8), and
the target tests the loop's exit at the head (`slt; beqz exit`) with a `j` back, where `for`/`while` roll the test.

By hand: the commands as macros on `gfx++` and `while (1) { if (i >= count) goto done; ... i++; dots++; } done:`
(or `if (i < count) { ... } else break;`) scored 0. The same with an indexed `dots[i]` stayed at 1730, and a `for`
loop at 3595 to 4585.

From the saved draft (5445), by library families, one step each:

1. `returned_gfx_loop` (new): the commands on one advancing cursor, 2840; with `loop_else_break` in the same child
   (`returned_gfx_loop_else_break`), 405.
2. `or_chain_order`: 205.
3. `statement_slide` (the counter's `= 0` below the header commands): 0.

Failed experiments: `-O1` (6435) and `-O0` (15261) on the saved draft; KMC GCC 2.7.2 on the indexed rewrite (4640).

Controlled: the unit test in decomp-repair. The scores are this one function. That the exit test stays at the top
because the compiler does not roll a conditional whose jump leaves for an `else` or a user label is a hypothesis (no
source for SN's builds).
