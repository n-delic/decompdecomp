---
title: exit guard sink declaration initialiser
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A second 15-instruction bounds-checked table getter, the neighbour of the one in
`function/table-getter-base-set-below-guard.md`, under the same compiler and flags. Its saved near-miss also set the
table's address above the early-return guard.

The source change, and the measured result before and after (scores, the judge used):

The same one statement moved below the guard: 60 -> 0 (`fast_judge.py`, then the app's `compile_and_diff`). The same
value written in the declaration (`char *base = table;` above the guard, a control spelling, not a saved source) also
scored 60. Declared without the value and assigned below the guard: 0. decomp-repair `exit_guard_assignment_sink`
splits such a declaration since decomp-repair PR 1980.

Failed experiments that would otherwise be repeated:

- On the declaration spelling, 11 children of `guard_shape`, `early_return_guard_block`, `symbol_base_inline`,
  `symbol_address_local`, `temporary_hoist`, `declaration_scope_sink` and `member_array_rows`: none below 60.
- From the form with the symbol named at its use (450), `symbol_address_local` is already exact: it places the local
  below the guard. Only a source that already holds the local above the guard needed the new family.
- The inverted form (`if (i < count) { return ...; } return 0;`, 320) needs no new family: `guard_shape` is exact.

What is controlled evidence and what is one function:

One function; the declaration spelling is a controlled variant of it.
