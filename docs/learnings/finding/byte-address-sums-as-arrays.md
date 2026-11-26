---
title: byte tables walked by address sums, written as arrays and records
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 table copies (80 and 68 instructions) with nested loops, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`.
Saved and plateau sources read globals as `extern u8 D[];` and added scaled indices by hand (`D + row * 0xE0 + k * 0x10`,
row pointers, `base + i * 4 + 0x11C`). Many sessions stayed at register-allocation rows under both functions.

The source change, and the measured result before and after (scores, the judge used):

Declaring every table as what its strides say (`u16 D[][14][8]`, `u8 D[][8][0x22]`, `u16 (*P)[320]`, one structure for
a source block, an array of records for offsets within one stride) and, for the first function, `u8` for the parameter
and return, was exact (app judge, 0). decomp-repair `linear_subscript_view`: the first function's plateau 2325 gave
290 as one child, from which `entry_parameter_copy+parameter_width` and `argument_slot_drop` reached 0; the second's
plateau 1720 gave 0 as its first child.

The second function's destination index is `i * 32 + (31 - j)` in a 1024-element row; written
`(i * 32 + 31) - j` it scored 295 (GCC folds it to `i * 32 - (j - 31)`). `temporary_hoist` reaches 0 from there.

Failed experiments that would otherwise be repeated:

Declaration order, do-while loops, masking the parameter at each use, and `-O2`/`-fschedule-insns` (all worse than
`-O1`). `u16 D[][2][32][32]` with `[i][31 - j]` for the second function scored 1080.

What is controlled evidence and what is one function:

The scores are the app judge's on these two functions. Why the array form gives the target's registers (the loop pass
hoisting each row address) is a hypothesis, not read in the compiler's source.
