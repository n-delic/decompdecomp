---
title: struct field copy address fold
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 616-instruction N64 save-menu state machine, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Twice it
copies a 0x20-byte record field out of a global array of 0xB4-byte records into a global: the target computes
`v0 = (&records + 0x54) + k * 0xB4` (the field offset folded into the `%lo` of the symbol) and loads the copy's words
from `0(v0)` .. `0x1C(v0)`.

The source change, and the measured result before and after (scores, the judge used):

`dst = records[k].field;` compiles with the offset in every load (`lw t1, 0x54(v0)`, v0 = &records[k]). Written
`dst = *&records[k].field;`, the address goes through `ADDR_EXPR`, where `plus_constant` folds the constant field offset
into the symbol, and the block move then starts at offset 0. App judge 4110 -> 685 for the two copies (fast_judge),
and the function matched after one more change (pointer walks for two clear loops). decomp-repair rule
`struct_field_copy_fold` (JSON rule) proposes it for every `d = A[i].f;`.

Failed experiments that would otherwise be repeated:

A pointer local (`Blob *src = &records[k].field; dst = *src;`) also folds the offset but changes the register
allocation (2350): keep the expression in the assignment.

What is controlled evidence and what is one function:

Controlled: the only edit between the two scores is `*&` on the two copies. One function.
