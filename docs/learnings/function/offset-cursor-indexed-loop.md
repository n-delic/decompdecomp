---
title: offset cursor indexed loop
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
An N64 function (about 30 instructions) that calls a helper and ORs together an eight-word global flag table. The saved
best was m2c's draft (judge 125): a `do`/`while` with a counter `n` and a byte-offset cursor `off`, the cursor set to
`n * 4` at the bottom of each pass and the read written `temp = *(s32 *)(off + &TABLE); acc |= temp;`. KMC gcc 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`.

The source change, and the measured result before and after (scores, the judge used):
The cursor and its setup removed, the counter indexing the table: `temp = ((s32 *)&TABLE)[n];` in the same `do` loop
(125 -> 0), or `for (i = 0; i < 8; i++) acc |= ((s32 *)&TABLE)[i];` (0). All of these were exact: the `for` loop, the
`do { ... i += 1; } while (i < 8)` loop, each with the element read into a local first and read in the `|=` itself. So
the cursor was the whole difference: the loop form and the temporary do not matter here. The decomp-repair family
`offset_cursor_indexed_do` / `offset_cursor_indexed_for` (module `offset_cursor_indexed_loop`) emits the `do` and `for`
forms. Proof: `prove_chain.py` on the pre-match source from the attempt history (`--families all`, and with the two
families named) reaches 0 in one step; before the family, no child of 352 improved on 125.

Failed experiments that would otherwise be repeated:
Every family that existed before (352 children from 78 scored) left 125: `table_cursor_index`, `walk_counter_for` and
`stepped_cursor_index` all fold a second *pointer* cursor beside the counter, and none reads `cursor = counter * size`
with the cursor an integer byte offset.

What is controlled evidence and what is one function:
One function (the judge, not a controlled experiment). Hypothesis, not read from the compiler's source: the cursor's
multiply and add are a separate register the loop pass cannot reduce, where the indexed read is a plain induction
register it strength-reduces to the target's pointer step.
