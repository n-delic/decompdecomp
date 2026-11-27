---
title: A table subscript whose column byte loads after the row shifts, by naming the row product (590 to 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 174-instruction N64 per-state setup
routine, KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1`. Four `table[id * 0x110 + sel->col]` reads, three of them call
arguments, with `sel` a pointer to a small global record.

The source change, and the measured result before and after (scores, the judge used): `table[(row = id * 0x110) +
sel->col]` with one `s32 row` for every site: 590 -> 0 in the app's judge (family `subscript_row_stage`). Before that, `do
{ sel = &record; } while (0);` kept the field reads base-relative (1520 -> 600; the library's `local_weight_ladder` finds
a form of it), and the frame needed an unused 0x18-byte local array (scaffolding, or locals the C does not show).

Failed experiments that would otherwise be repeated: the 2-D array and struct-row declarations, casts, shifts and both
operand orders scored 590 or far worse; `row = ...;` as a statement before each read scored 30; volatile fields worse.

What is controlled evidence and what is one function: one function; the reason (the assignment forces the product into a
register before the other operand is loaded) is a hypothesis.
