---
title: a 445-instruction actor picker: typed table rows, a split guard, and the order loop.c hoists its invariants
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

An N64 function that picks the nearest of four actors, then walks a list as a fallback (a loop over a four-entry
table with a float distance, then a switch over the list nodes' kinds), 445 instructions, `gcc-2.7.2-kmc -G0 -mips3 -mgp32
-mfp32 -O2`, saved best 3336 (2016 recorded by an earlier scoring). m2c's structure plus a search's scaffolding: temporaries
for every flag test, `if (x) { S } else { S }` arms, byte-offset table reads, a `var_s5 = (u16) i` clobber inside a
counting loop. 3336 -> 0, `setup.py try` accepted it byte-identical.

The source change, and the measured result before and after (scores, the judge used; standalone judge, one edit after another):

| edit | score after |
| --- | --- |
| saved best | 3336 |
| the counting loop as a plain `short` counter, no `var_s5 = (u16) i` | 3128 |
| `if (temp_s4 & 2)` instead of the byte table's second read | 3113 |
| flag tests written as `if (temp_s4 & 4)` (no `var_v0_N = flags & K` temporaries, no early `flags & 8`) | 2593 |
| the identical arms dropped; the final return through the pointer's own variable | 2563 |
| `u8 sSaveSlotTable[][24]`, `table[i][0]` (the hoisted `la` gone) | 1778 |
| that loop a `do { } while` after a guard that reads `*(s32 *)(gSceneType + 0x14)` | 1343 |
| the three invariant pointers declared in the target's priority order | 1153 |
| a callee's first argument dropped (the neighbour's matched C has two parameters) | 1043 |
| the three straight-line tables as one-field records (the 8-byte frame hole) | 1015 |
| the guard reads the global and the loop bound is assigned after it, and the load before the float guard's last test | 705 |
| the switch's value tested once after it (arms' `v = a3 & 0xFF` and the `goto`s dropped) | 685 |
| the product `d = call() * w` written to the other loop's float, not the operand | 680 |
| the constant 3 held in the finished counter (`n = 3`) set before the pointers, which are assigned after | 0 |

Library: `record_slot_rows` (rows, 2028 -> 1015), `guard_load_split` (1015 -> 705 as one child), `switch_join_condition`
(705 -> 685), `float_product_retarget` and `loop_constant_holder` (685 -> 0, `prove_chain` reaches 0 with exactly those two).
The earlier edits (the counting loop, flag tests, the pointer order, the callee's arity) were not found by the library.

The rules behind the last four rows: stale frame slot, see byte-arithmetic-table-reads-leave-a-frame-hole.md. loop.c emits the
hoisted sets in the order the locals were created, and global.c ranks them by `floor_log2 (refs) * refs / live`: the literal
held in a finished counter shares that pseudo's references from its first loop, so it takes `$s1` and is emitted first, the
pointers fall in `$s4`/`$s5`/`$fp` in the order they were assigned. A literal that is its own new local ranks last (its
live range is the longest and its references the same) and took `$s5`. `expand_binop` swaps a commutative operation's
operands when the destination is the second operand (`mul.s $f20,$f20,$f0`), so a product written back into its operand
cannot have the call first.

Failed experiments that would otherwise be repeated:

Reading the saved best as a clean rewrite (`plain.c` 3791) was worse than the best; the identical-arm `if (p) {A} else {A}` was
needed until the returns were rewritten; a fresh `f32` for the product put it in `$f0` (a short, block-local pseudo), not in the
saved `$f20` the target has; `three` as a new local, in any of 24 declaration orders with the pointers, never got the
priority the counter's pseudo does; `FIELD()` and `*(T *)(table + e * K)` spellings of the three tables all leave the
stale slot (only a struct-array access avoids it).

What is controlled evidence and what is one function:

One function (every row). The two compiler effects it ran into are controlled on toys in the finding above.
