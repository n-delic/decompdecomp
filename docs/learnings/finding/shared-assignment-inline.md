---
title: m2c's argument local assigned the same expression at several places: its operand gets extra references and loses its register to a constant
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-cse-skip-blocks
evidence: observation
date: 2026-11-22
---

A 490-instruction menu function (a N64 game, KMC GCC 2.7.2, per-function `-fno-cse-skip-blocks`) sat at 60: every row
was a register difference, `$s5` and `$s6` swapped (a call's result held for the function, and the constant `1` two
calls pass on the stack). The saved best was m2c's: a local `var_a0_7` assigned `temp_s6 - 0x28` at four places (the
two arms of an empty-looking `if`, a nested arm, an `else`) and read as the first argument of two calls. The assembly
has three `addiu $a0,$s6,-0x28`, but they are copies gcc made into branch delay slots, which is what m2c read as
several assignments; the game's source wrote `temp_s6 - 0x28` once in each call.

What the allocator saw (`passes.py`, the `-dg` table): with the local, the call result had 9 references over 221 live
insns (priority `floor_log2(9) * 9 / 221` = 0.122) against 3 over 28 (0.107) for the constant, so the result took `$s5`
first. Written into the calls it has 7 references (0.065), the constant is allocated first and takes `$s5`.
Removing the local, its four assignments and the empty `if`/`else` was the whole edit: 60 -> 0 with the app's judge,
no other change. Hypothesis, supported by the table and by `global.c`'s `allocno_compare` (not by a pass that
isolates the reference count).

Failed before it: every hand edit that changed the constant's life (`do { } while (0)` around either call, which
weights its references and put it above everything, `s0`), swapped constants, nested `if`s, a natural
`v >= 0 && (v < 2 || ...)` condition (which changes the compare chain); all 373 children of the existing families.
The loop weight only fits a window of priorities (0.122 to 0.158 here), which is why a weight on the constant did not
work and fewer references on the other value did.

Library family `shared_assignment_inline` (decomprepair 0.5.646) writes the expression where it is read, for a local
whose assignments all carry one plain expression of parameters and singly-assigned locals; offered with and without
the emptied `if`. Both children score 0 from the saved best. One function; the rule that extra references move a
priority is from the compiler's source and several earlier entries.
