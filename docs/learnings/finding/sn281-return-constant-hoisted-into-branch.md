---
title: A return constant set before a branch to the epilogue (jump2's x = a hoist)
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 108-instruction float search loop in an N64 game (SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp32 -mno-split-addresses`; `-G4` in place of `-mno-split-addresses` is exact too). Inside a
`do { } while (1)` loop an early return of a constant, `if (diff < 0.0 && !(p->flags & 1) && t == 0.0) return 0.0f;`,
was followed by the loop's update `step *= dist / len;`. The target keeps the return as its own block,
`bc1fl L_div (div.s $f0 in the slot); mtc1 $zero,$f0; j epilogue`, and the flags test before it is `bnez $v0, L_div; nop`.
Ours set the return register before the branch and jumped straight to the epilogue:
`mtc1 $zero,$f0; bc1t epilogue`, and the flags test became `bnel` with the `div.s` stolen into its slot.

The rule (jump.c, the block "Simplify if (...) { x = a; goto l; } x = b;"): after reload, jump2 turns
`if (!c) goto L; x = a; goto end; L: x = b;` into `x = a; if (c) goto end; L: x = b;` when `a` is a constant or a
register and the first active insn after the `goto` sets the same hard register `x`. Here `x` is `$f0`: the return
value, and the division's result, which the allocator also put in `$f0`. The target had something else first after
the label, so the hoist did not happen there.

The source change, and the measured result before and after (scores, the judge used):

The counter increment `n++;`, written before the zero test, moved to just after it (before `step *= dist / len;`). The
label's first insn is then `addiu $s0` (not a set of `$f0`), jump2 leaves the return block alone, reorg puts the
increment into the earlier `bc1f`'s delay slot (it runs on both ways out) and fills the zero test's `bc1fl` from the
division: 760 -> 0 with the app's judge (the function's profile applied through `compiler_experiments` and `suggest`).
This is the move decomp-repair `assignment_guard_sink` describes (an assignment sunk past the guard that follows it);
whether it offers it on this shape (a counter before an early-return guard inside a loop) was not checked.

Failed experiments that would otherwise be repeated:

- Every spelling of the return itself scored the same 760: `return 0;`, `nt = 0.0f; return nt;`, a result variable
  with `goto end`, `break` with the zero return after the loop (820), nested `if`s, `if (...) return 0.0f; else step
  *= ...`.
- `len = dist / len; step *= len;` (reusing a long-lived local for the quotient) also stops the hoist (415) but puts the
  quotient in `$f6`, not the target's `$f0`.
- No flag changes it: `-fno-thread-jumps`, `-fno-expensive-optimizations`, `-fno-cse-follow-jumps`,
  `-fno-cse-skip-blocks`, `-fno-rerun-cse-after-loop` all left 760.

What is controlled evidence and what is one function:

The jump.c rule is read from the 2.8.1 source and seen in `cc1n64.exe -dJ` (the `f0 = 0.0` insn moved above the branch
in the `.jump2` dump, absent in `.jump`). That the increment's position is what stops it is one function, verified by
the app's judge.
