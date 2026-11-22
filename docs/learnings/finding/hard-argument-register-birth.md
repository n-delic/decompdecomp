---
title: hard argument register birth
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

**Function and compiler.** A 117-instruction N64 menu function, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, saved best
120, every instruction right, four rows reordered: after `jal` (a helper returning a count), the target has
`sll $a2; addu $a2; sll $a2; lhu $a0; andi $a2,0xfc; lbu $a1; addiu; andi ...` and ours had `lhu $a0; lbu $a1` ahead of
the three-instruction `*12`. The block (`width = (f(obj) * 12) & 0xFC; g(x, y, width);`) was a labelled arm that the
other `case` of a `switch` reached with `goto`.

**Cause (sched.c read; the `-dS` ready lists confirm).** The first scheduler works backwards. `adjust_priority` gives an
insn the launch priority when `birthing_insn_p` holds: it sets a register that is live and `reg_n_sets[regno] == 1`. For
a HARD register `reg_n_sets` counts every set in the whole function, so the `and` into `$a2` for the third argument was
a birth whenever this was the only call with three arguments in the function: it was picked at once (placed right
before the call), pulled the `*12` chain with it (the chain's temporaries are single-set pseudos, also births) and the
argument loads (priority 3, not births) landed ahead of them. Other attempts that only changed the chain's pseudos
(multi-set variables, explicit shifts, statement orders, a 720-order grid, argument temporaries) moved the sched1 order
but never gave the target's final order: the birth of `$a2` is what decided it.

**Fix (one edit).** The block written in both `case`s (the `goto` replaced by a copy of the arm's run). `$a2` is then
set twice, the insn is no longer a birth, and the loads' own priorities decide. Cross-jumping after register allocation
merges the two identical tails again: 120 -> 0, byte-identical in the app. The original game source presumably had the
block twice (a macro per case). decomp-repair `goto_arm_label_copy` does it from the saved best by itself.

**Failed experiments.** Statement-order grid of the chain and both argument computations (720 orders), `do { } while (0)`
before and inside the chain, the chain as `x * 3 << 2` or as shifts on a multi-set local, typed argument temporaries,
the width in a multi-set variable of its own (all 120 or worse; 750 when it reused the earlier `width`).

**Evidence level.** One function; the sched.c `birthing_insn_p` reading and the ready lists are the mechanism.
Hypothesis for the general case: any argument or result register that a single call site in the function uses is a
birth for sched1, so a third-argument setup (or the fourth) behaves differently from the first two (set everywhere).
Library family: `goto_arm_label_copy` (decomp-repair, declares itself to the registry).
