---
title: display state updater base locals and shared slot
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags:

A 443-instruction display-state updater (one outer loop over a few save slots, a record per slot with three label pointers, two
float-to-unsigned conversions, two `sprintf` formats, an inner loop over the other slots that steers a label and calls one of
three bank entries). KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`. The saved best was an m2c draft at 6171 (254 rows): every
extern a scalar `s32`, so `(i * 0xC) + &D_x` scaled by four; stray arguments on the one-argument callees; `register`
temporaries; the slot index and the record pointer in two variables; the three-way pick of a bank entry as one variable and
one call.

The source change, and the measured result before and after (isolated judge, then the app: 0, matched; each row is the score
after that step with the earlier rows in place):

| step (saved best -> match) | score | what it changes |
| --- | --- | --- |
| clean rewrite: typed externs and records, stray callee arguments gone, `u16` counters, `(u32)` float conversions | 6171 -> 2640 | the strides, widths and addresses the target used |
| the call made in each of the three leaves instead of one call after a variable (`nested_assign_call_arms`) | 2640 -> 1515 | the shape is `v = A; if (c) { v = B; }`, which `call_into_arms` does not read |
| the slot's byte offset and entry pointer in locals before the inner loop | 1515 -> 970 | later undone: the last rows wanted them back inline |
| one label local per string block (`local_web_split`) | 970 -> 930 | one variable held three pseudos of one priority |
| the second load of the record's info pointer into a local of its own | 930 -> 750 | the draft already had it; the clean rewrite had merged the reload |
| the slot index written into the record's variable (`pointer_integer_phase_union`) | 750 -> 605 | the shared pseudo has 60 refs / 320 live (0.94) and ranks below the `idx * 4` temporary (1.04): the temporary takes `$s2`, the record `$s3`, the slot reuses `$s3` |
| `v = 0; if (m) { v = x; }` as `if (m) { v = x; } else { v = 0; }` (`sink_zero_initialization`) | 605 -> 405 | a plain `beqz` with the constant in its delay slot, not a `beql` |
| the two scaled record offsets in locals on the line before (`scaled_offset_local`) | 405 -> 395 | the record register comes first in the sum |
| `k = 0;` above the guard of the inner loop (`loop_counter_init_hoist`) | 395 -> 230 | reorg fills the guard's delay slot from before it |
| the slot's table entry and rows as `TAB[slot]`, `ROWS[k][slot]`, the locals inline (`slot_row_index`) | 230 -> 25 | loop.c hoists `slot << 2` once and both users share it; the entry pointer alone made it 715 and the byte sum rows alone 205 |
| the countdown table's address in a local before the slot is taken (`table_base_local`) | 25 -> 5 | `$v0` for the table and `$v1` for the shifted index, as the target |
| the array's own address in a local and indexed (`field_array_base_local`) | 5 -> 0 | `addu $v0, $s3, $v0` with the record first |

The families in the table are in decomp-repair; `nested_assign_call_arms`, `scaled_offset_local`, `slot_row_index`,
`table_base_local`, `field_array_base_local` and the cast-store form of `float_unsigned_conversion` are new with this match.
Each step was reversed on the matched source and every family asked for children (`prove_chain`'s generator, 800 children each):
the reversed `else` (200), `local_web_split`'s (55), the counter init (105) and the shared slot (160) were each found by an
existing family at 0; the scaled offsets (270 and 5), the array base (5), the table address (20), the call leaves (580), the row
index (365: 205 after the existing families) and the float stores (1915: 1710) were not. From the state with the table, array
and offset steps all undone (35) the three new base-local families go 35 -> 15 -> 5 -> 0 by themselves, because the offset
lines are also offered above the array pointer lines of the same statement (the order the match needed).

Failed experiments that would otherwise be repeated:

- The hoist of `D_x`'s address (`lui $fp`) and of `-0x100` flipped with the loop's instruction count (432 vs 436 real insns:
  `threshold * savings * lifetime >= insn_count`, loop.c): padding the loop to unhoist them hid the real differences, and they went
  away by themselves once the rest of the shapes agreed. Read the loop dump (`-dL`) before adding a statement to move one.
- The slot's entry as `*ps` from a pointer set before the loop, or the rows as a byte sum, each alone was worse than the draft
  (715 and 205); only together with the index forms was it 25.
- Naming the second product (`defKind * 2`) in a local made the operand order right and the registers wrong (780): the array base
  in a local was the form that fixed the order. Declaration order and `register` hints never moved the saved-register swap; the
  priorities did.

What is controlled evidence and what is one function:

The scores are the isolated judge on this one function and match the app's verdict (matched, 0, 443 instructions). The
pointer/integer variable and the loop hoist threshold are one function's observations; the base-local steps were each reversed
and re-run through every family (above). Scaffolding the final source relies on: the record pointer variable holding the slot
index through casts (`ent = (Ent *) (u32) i;`, read back as `(u32) ent`), and a few locals the compiler never needs (`t3`, `o2`,
`a1`, `ps`, `off` declared and unused).
