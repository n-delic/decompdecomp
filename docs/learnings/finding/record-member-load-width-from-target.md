---
title: A machine draft reads record members at the wrong width
kind: finding
compiler: gcc-2.7.2-kmc
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1`. A display list builder reading a record of 0x28 bytes (halfwords) and a
camera record of nine floats, calling guPerspective and guLookAt.

The source change, and the measured result before and after (scores, the judge used):
- Machine draft, then library steps (float_argument_bits, symbol_access_width, parameter_read_scope): 1210.
- Members spelled `*((M2C_UNK *)((s8 *)&SYM + 0x210A + o))` and `*(f32 *)(...)` given the target's `lh`, the float ones
  as `(f32) *(s16 *)`: 1210 -> 0 (the app's judge). decomp-repair `asm_offset_read_width`.
- By hand: records and float prototypes declared (`View views[]`, `Cam D[]`, `(*list)++`) was 0 at once, 6121 at -O2.

Failed experiments that would otherwise be repeated:
Notes from an earlier attempt: stuck at 120 on the order of guPerspective's argument loads with m2c's offset spelling.

What is controlled evidence and what is one function:
The 1210 -> 0 step is controlled; one function.
