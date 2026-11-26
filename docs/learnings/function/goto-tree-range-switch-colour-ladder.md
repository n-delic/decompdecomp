---
title: A tree of range tests with a bltz is a switch with case ranges
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 96-instruction colour ladder (gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`, exact; the saved best was 1760).

The target tests `slti` bounds in a balanced order and has one `bltz` on a value loaded with `lbu`: GCC's decision tree
for `switch (v) { case 0 ... 0x77: ... case 0xDC ... 0xFF: ... default: report(); return; }` (GNU case ranges). m2c
reads it as nested `if`s with `goto`s into one arm's tail, and every earlier attempt kept that shape.

- Switch by hand over a record array: 10; the three stores of the override block in the order the target has them: 0.
- From m2c's draft: 5781 -> 1205 (decomp-repair `goto_tree_range_switch`) -> 305 (`m2c_refinement`) -> 200
  (`narrow_widening`) -> 0 (`record_slot_table`).

Evidence: the app's judge. Failed: nested `if`s with a `goto` for the bad value (1760 to 4430), under -O1 or -O2.
