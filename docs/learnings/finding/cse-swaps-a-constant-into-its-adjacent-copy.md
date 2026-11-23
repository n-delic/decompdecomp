---
title: cse swaps a constant into its adjacent copy
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A pair of float locals per screen dimension in a 1133-instruction effect spawner (KMC GCC 2.7.2, `-G0 -mips3 -mgp32
-mfp32 -O2`): `height = 120.0f; var_fs1 = height;` at entry, `var_fs1 = 60.0f; height = var_fs1;` in a switch arm. The
target loads the constant into the register of the variable the source assigned it to (`mtc1 $at, $f28; ... mov.s $f22,
$f28` at entry, `mtc1 $at, $f22; mov.s $f28, $f22` in the arm); the candidate had the entry the other way round.

The rule, read in cse.c (`cse_insn`, "Special handling for (set REG0 REG1)"): for a register copy `REG0 = REG1` whose
destination is the canonical register of REG1's class, when the insn right before it (notes aside) sets REG1, cse
rewrites the pair so the earlier insn sets REG0 and the copy becomes `REG1 = REG0`. The canonical register
(`make_regs_eqv`) is the one that lives past the current block and whose last mention (`regno_last_uid`, a set or a
use) is latest. So `A = K; B = A;` comes out as `B = K; A = B;` exactly when B is mentioned later than A, and only
when the two statements are adjacent.

The source change, and the measured result before and after (scores, the judge used):

`height = 120.0f;` moved above the three statements between it and the previous pair, leaving `var_fs1 = height;` where
it was: the copy no longer follows the set, the constant stays in `height`'s register (`$f28`) as the target has it,
80 -> 70 with the app's judge. The arm whose copy is written as part of the same statement (`a = (b = K);`) and the
arm with other statements between the set and the copy behave by the same rule: adjacent and the copy's destination
mentioned later swaps them, otherwise not. decomp-repair `statement_slide` offers the move once float literals count
as constants (#1539).

Failed experiments that would otherwise be repeated:

Writing the entry pair in the other order (`var_fs1 = 120.0f; height = var_fs1;`) compiled identically: the swap
undoes it when the two are adjacent. `register`, the 24 declaration orders and the case-2 spellings were identical too.

What is controlled evidence and what is one function:

The rule is read in the compiler source; the measurement is one function.
