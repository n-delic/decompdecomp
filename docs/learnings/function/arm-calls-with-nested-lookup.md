---
title: arm calls with nested lookup
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like: a 440-instruction handler (a three-case `switch` on a record's kind, two objects
reached through `+0x6C` pointers, a `sprintf` of two table lookups in two places, a clamp after each table update),
KMC GCC 2.7.2. The saved best was 1605 (33 differing rows), written from m2c's draft with a use-before-assignment
pointer local, shared scratch locals between the cases and a `sprintf` after each `if`/`else`.

The source change, and the measured result (judge: `fast_judge`, then `compile_and_diff`, matched, 1605 -> 0):

1. **The `sprintf` goes into each arm with the first lookup written into it** (the last 3 rows, `move $s0, $v0` in each
   arm where the target has it once in the tail, in the delay slot of the second `jal`). m2c reads the merged tail back
   as `name = lookup(id | 0x9000); k = 0xA2;` in each arm and `sprintf(buf, fmt, name, lookup(k))` after the join. With
   the variable gcc copies the call's result in each arm; written as the game had it,
   `if (g) sprintf(buf, fmt, lookup(id | 0x9000), lookup(0xA2)); else sprintf(buf, fmt, lookup(id | 0x9000),
   lookup(0x16A));`, the result stays in `$v0` to the join, cross-jumping merges the second call's setup and the copy
   ends up once, after the join. `call_into_arms` alone leaves the variable (the code does not change), the inline
   alone has nothing to inline: decomp-repair `call_into_arms_inline` makes both as one child.
2. **A pointer local assigned in the use** (`(*(u16 *)((char *)(p = base + i * 2) + 0x1BC)) += tbl[i * 2]`): m2c printed
   the compound assignment before the line that assigns `p`, so the saved best read an uninitialised register; the
   assignment goes first or into the use (`assign_after_use_swap`). Alone it scores worse (1605 -> 1805); the
   register rows only move once the other edits are in.
3. **Pointer locals for every nested sum** (`pA = base + i * 2;` before the statement, `(char *)(pA)` in it): gcc puts the
   base first in a sum assigned to a variable (`addu $v0, $s1, $v0`) and the scaled index first in one nested under a
   dereference (`addu $v0, $v0, $s1`). One site at a time moves nothing (`address_temporary`); all five together are
   `deref_sum_local`.
4. **The case's own scratch**: m2c reused the function's scratch `temp_s2` in the first case (`temp_s2 = (u16)idx`);
   the target keeps that value in a temporary register and leaves `$s2` to the second case. `local_web_split` declined
   because the web ends in a `goto` to the common exit; it now splits a web whose gotos land past every mention of the
   local.
5. **The loaded value is assigned to the scratch the arm reads** (`temp_s2 = temp_v1_7;` first in the arm, its reads
   written on `temp_s2`): the target compares the load's register and `move $s2, $v1` sits in the branch's delay
   slot (`arm_local_into_scratch`). Splitting the first case's web (4) swaps `$s1`/`$s2` (11 -> 34 rows) until this
   one is in (3 rows): they only pay off together.
6. `flag_web_narrow` (existing: `s32` -> `s8` on the `hit` flag, the target copies `move $v0, $s6` before each test) and
   a signed table value added into a `u16` store: the table read is `lh` in the target and `lhu` in `+= tbl[i * 2]`
   (combine drops the sign extension); read through an `s32` local first it keeps it (`narrow_table_addend`, in the same
   child as the pointer-assignment fix: the swap alone does not change the code). The saved match used the existing
   `halfword_store_normalization`'s inline helper for this; the `s32` local is the same bytes with no scaffolding.

Measured, rows of difference (target vs candidate): 33 saved best, 35 (2), 27 (+6), 15 (+2), 11 (+3), 34 (+4), 3 (+5),
0 (+1). From the saved best the existing families reached 750 (24 rows) and stopped. With the new ones
(decomp-repair #1344: `call_into_arms_inline`, `assign_after_use_swap`, `narrow_table_addend`, `deref_sum_local`,
`arm_local_into_scratch`, and `local_web_split` across a forward `goto`) `prove_chain.py ... --combine 12` is greedy and
reaches 0 in six steps: 1605 -> 1025 (assign_after_use_swap with the wide local) -> 515 (flag_web_narrow) -> 255
(call_into_arms_inline) -> 40 (arm_local_into_scratch) -> 20 (deref_sum_local) -> 0 (local_web_split).

Failed experiments that would otherwise be repeated: declaration order of the pointer locals (no effect on the operand
order of the sum, tried before the pointer locals); a single `j` local for both enemy indices with the second load in
`temp_v1_7` and no copy (the load gets a saved register of its own, `$s4`); the first lookup before the `if` (the call
is then made once and the `g` test follows it, a different layout).

What is controlled evidence and what is one function: the `sprintf` in each arm is a controlled toy
(`d.c`-style: two arms calling `sprintf(buf, fmt, lookup(x), lookup(K))` give exactly the target's arms, and the
variable form gives `move` in each arm). The rest is one function; each rewrite's mechanism (operand order from the
assignment, the web split) is a hypothesis from the measured rows, not read from the compiler source. The saved match
keeps the scaffolding `__repair_store_halfword` (an inline sign-extension of a halfword) and an unused
`__repair_addr` local from the chain.
