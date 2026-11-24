---
title: arm local parameter default
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

A small N64 collision test (86 instructions, KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) takes a record pointer as
its second parameter and, when it is NULL, builds a temporary record in a local array and walks that one instead. m2c
wrote the pointer the loop walks as a local that only the NULL arm sets (`if (arg1 == NULL) { ...; var_a1 = sp; }`):
on the other path the local is never set, because the original program used the parameter itself, in the same
register.

The source change: `var_a1 = arg1;` before the test. The target keeps the pointer in `$a1`, the loop counter in `$a2`
and the record copy in `$a3`; the arm-only version had `$a3`, `$a1`, `$a2` (register rows only, 75 -> 0, isolated
judge). Every older family stayed at 75 (296 children, `--children 400`, `--combine 10` with lookahead): none offers the
copy. The decomp-repair family `arm_local_parameter_default` does (`local = param;` before the test, the declaration
initialiser, the other arm's copy); `prove_chain` reaches 0 from the arm-only start in one step.

Failed experiments: the same assignment as `else { var_a1 = arg1; }` after the arm scored 560, so the placement before
the test matters. Hypothesis, not checked in the compiler's source: the assignment in an arm of its own is a second set
of the pseudo whose range starts after the test, where the copy before the test makes it live from the entry, in the
parameter's register.

Controlled evidence versus one function: one function; the false-positive load over a project's sources without
judging was 0 of 1033 matched sources, 2 of 1650 m2c drafts and 1 of 1956 saved bests.
