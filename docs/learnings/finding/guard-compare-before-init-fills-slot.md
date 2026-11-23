---
title: A guard's compare computed before the counter init: the init fills the delay slot and the compare is not folded
kind: finding
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3
evidence: observation
date: 2026-11-23
---

A 673-instruction collision walker (KMC GCC 2.7.2, `-O2 -G0 -mips3`), two arms (a 16-bit and an 8-bit variant of one
loop), each `if (flag < len) { i = 0; p = q + 4; do { ... } while (...); }` where `flag` (`x & 0x400`) is known zero from
an earlier branch. The target compares `sltu $v0,$s3,$v0` (the flag register, not zero) and has `move $s0,$zero` in the
branch's delay slot.

Three spellings, same function, measured with the app's judge:
- init in the body (m2c's): the slot keeps a `nop` (reorg did not take the `move` from the fall-through; the assembler
  adds the nop), the `sltu` stays. Score 285.
- init hoisted before the `if` (bare, or in a `do { } while (0)` fence): the init fills the slot, but cse had seen
  `i == 0` and replaced the compare's `flag` by the newest register of the class of zero (the counter), which a later
  pass folds to `n != 0`: the `sltu $v0,$s3,$v0` is gone, and two dead compare pseudos appear (see
  dead-guard-compares-pad-the-frame). The fence does not stop it.
- compare into a local first: `g = (flag < len); i = 0; if (g) { ... }`. cse writes the compare before it knows the
  counter is zero, so the `sltu` keeps `flag`; the init stands before the branch, so it fills the slot. Score 85, every
  remaining row a register choice.

Reading (cse.c `fold_rtx` / `make_regs_eqv`: a register is replaced by the newest register equal to it; hypothesis
that the measurement agrees with). decomp-repair `guard_compare_first` makes this child (merged); it offers each guard,
neighbouring pairs and all of them, with the new local at the top and after the declarations, and only when the first
mention of the counter before and after the `if` is a plain assignment.

Failed: moving the init into a one-statement `do { } while (0)` before the `if` (`branch_slot_scope`'s shape) did fill
the slot but folded the compare; sinking the pointer init before the counter init put a pointer `addiu` in the slot,
which reorg then refused (the pointer register is live at the branch target).

Controlled evidence: the function above (285 -> 85 with the family's child, the guard written the same way in both
arms). One function; the mechanism is read from the sources, not isolated on a toy.
