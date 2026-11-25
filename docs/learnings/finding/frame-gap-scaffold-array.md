---
title: frame gap scaffold array
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags: three N64 functions (a 190-instruction text
routine, a 330-instruction collision helper and a 110-instruction object behaviour; KMC GCC 2.7.2, `-G0 -mips3 -mgp32
-mfp32 -O2`) whose originals declared a local nothing reads (`char buf[0x20];`, `f32 spare[4];`, `char big[0xF8];`)
among real locals, arrays included. Without it the only differences were stack rows: `addiu $sp, $sp, -0x58` against
`-0x38`, and every saved register and local at another offset (22, 55 and 14 points).

The source change, and the measured result before and after (scores, the judge used): the matched sources with the
array deleted scored 22, 55 and 14; `frame_gap_scaffold` (decomp-repair 0.6.6, called from `diff_gated_repairs` with the
judge's rows) offers `char frame_pad[gap];` at each declaration position and scored 0 for each function, in one or more
positions (the array first, last or between locals). The gap is the difference of the two `addiu $sp` rows.

Failed experiments that would otherwise be repeated: `unused_frame_padding` and `inferred_frame_reservation` offer one
word or want a function with no stack objects of its own; `stack_gap_padding` wants m2c's `spNN` locals. None of the
families existed for a function that already has arrays.

What is controlled evidence and what is one function: controlled (the fix undone from three matched functions, the
family recovers it by the app's judge). The array is scaffolding (the original's was an unused local with another
name), reported as such in the match notes. Same session: `store_adjacent_gather` (a store moved beside the store of the
adjacent field, 1621 -> 0 on an object spawner) and `switch_byte_mask` (`switch (ch)` against `switch (ch & 0xFF)` on a
byte kept in an `s32`, 1595 -> 0).
