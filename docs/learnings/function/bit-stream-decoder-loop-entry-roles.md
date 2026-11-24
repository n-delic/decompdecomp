---
title: bit stream decoder loop entry roles
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 681-instruction N64 bit-stream decompressor of `bit-stream-decoder-guard-pointers-and-loop-made-registers.md`
(KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns`), resumed at the saved 215 (43 register rows).
Every kept source was built on the host beside the 215 source and run on 1500 generated streams (stored blocks, coded
blocks with canonical codes, codes past both first-level widths, matches with extra bits, table reuse, random tails
and random words); output, error word and every table compared. A deliberately wrong second-level step differed on
1287 of 1500. The `-8`/`-9` error paths are not reached by valid code lengths (the subtable total stays below 0x200 with
at most 511 symbols), so they are compared but not exercised.

The source change, and the measured result before and after (the app's judge-worker; 110 recorded by
`compile_and_diff`):

How the roles were found: `alloc_roles.py` lists each allocno's target register; a pseudo whose votes split was two
variables, and a block's value whose target register is a long-lived variable's was that variable. Then the `.lreg`
dump with `-g` (line notes) maps pseudos to source lines, and the `.greg` dump's `;; N preferences` lines show where a
register preference comes from (it moved with the last pass's loaded code: wherever that value lived, the variable
holding it preferred `$a0`).

| edit | what it is | alone from 215 | in the set |
|---|---|---|---|
| `clr_i`, `hist_i` | the first pass's clear and histogram loops count with `i` (target `$a3`, `i`'s register) | 620, 815 | needed |
| `x_i` | the sort pass's outer counter is `i` (it was shared with the last pass's loaded code) | 610 | needed |
| `a_k` | the sort pass's running count is `k` (target `$t3`, `k`'s register) | | needed |
| `x9_a` | the last pass's loaded code `D[k]` read into `a` (target `$a2`) | 730 | needed |
| `sh_a` | the inner loop's shift amount `(ent >> 4) & 7` set into the same `a` before the loop | | with `x9_a` |
| `j_y` | the sort pass's inner index is `y` (the distance entry's variable, target `$a1`) | 215 | 200 -> 155 |
| `ix_y` | the last pass's scatter index is `y` | | needed |
| `sb1` | the second-level base `tab + ((ent >> 7) + step)` through a pointer set after `ent` | | 410 -> 110 |

The four last-pass edits together: 215 -> 200; with `j_y`: 155; all nine: 110 (22 rows). Every subset of the nine was
scored (160 compiles): nothing below 110, and every edit is needed. The last step is decomp-repair's
`loop_subscript_base_pointer` (#1620), whose three children from the 410 source score 110, 110 and 405.

Why the roles matter (read from the dumps, a hypothesis for the compiler source): with the loaded code and the shift
amount in one variable, that variable outranks the inner loop's loop-made copy of the `D_800B283C + k` pointer and
takes `$a2` before it, so the copy gets `$t0` and the shift amount `$a2`, which is the target's colouring; split, the
copy (priority 4.0) took `$a2` first. Written inline, the second-level base is a loop-made register whose `srl ..., 7`
is emitted after the shift amount's; the pointer local puts it where the source writes it.

Still different at 110 (22 rows):

- the histogram increment's `$v0`/`$v1` (local-alloc; every spelling tried compiles identically: `+= 1`, `++`,
  `= 1 + H[x]`, a pointer, the symbol, `s32` index, `H[*q1 = x]`, the statement orders);
- the decode-table pass's mask: the target computes it from `tb` (`sllv $v0,$s7,$s3`); `(1 << tb) - 1` gives that
  but lifts `tb` (0.851) above the saved count `m` (0.818) and swaps `$s2`/`$s3` (195); `m = k` later, `c = tb` moved,
  the R8 spellings: no better;
- the last pass: a priority replay (`alloc_roles` with chosen priorities) puts every allocno in its target register
  when the base pointer ranks in (1.81, 1.9) and the inner loop's loop-made copy of `D_800B3134 + k` (11 refs over 15
  insns, 2.2) ranks at or below `off` (1.286). The copy's references come from the loop depth (set at depth 5, used at
  depth 6), so no spelling of the loop body found so far lowers it; a user pointer for it is made replaceable by loop.c
  and the copy disappears (1719, one register fewer in the frame).

Failed experiments that would otherwise be repeated:

- Declaration order of any local (30 positions for a split counter): identical code.
- The sort pass's pointer as an index, the count as an index, `q1` reused for the sort pointer, the guard as
  `n9 != 0`, the pointer inits swapped: 497-1155 at the 610 state.
- A fresh block variable for the last pass's loaded code: local-alloc gives it `$v1` and sched2 then moves the scatter
  index first (610); the shift amount as the loaded code's variable without `a`: 665.
- The phase loop or the main loop as goto loops: 2582-8361.
- The inner store's operand order (`D_800B283C[k] + ...`): 155; `*(sb + y)`, a `(u16)` cast, `y = y + ...`: identical.

What is controlled evidence and what is one function:

One function. The edits are role renames that the host check confirms keep every output; their combination was
measured exhaustively. `loop_subscript_base_pointer` reproduces the last step from the source before it (its tests in
decomp-repair). That block temporaries declared without an initializer (`u32 ix; ... ix = e;`) are not offered by
`block_temp_role_merge` (it needs `u32 ix = e;`) is a gap the `j_y` and `ix_y` steps fell into.
