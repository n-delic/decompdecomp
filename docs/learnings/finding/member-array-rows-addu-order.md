---
title: member array rows addu order
kind: finding
compiler: gcc 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-26
---

Three open N64 functions (a 33-instruction table initialiser, a 51-instruction record selector and a 23-instruction
setter) were each off by one operand-order row: the target's `addu $v1,$v1,$v0` (global pointer loaded first, then the
`sll $v0,$a0,4` index) against ours `addu $v1,$v0,$v1`. The source was `*(s8 *)(D + (i * 0x10) + K)` with `D` a global
holding a pointer. KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32`, app judge.

Hypothesis (not confirmed in the compiler source): pointer arithmetic expands the scaled term in address mode as
`(mult i 16)`, which the canonical rtl form puts first; an ARRAY_REF builds `array_address + index * size` with the
index already a register, so the base stays first.

The source change: reach the record through an array *member* of a structure, with the cast base:
`((struct R *)D)->e[i].f` where `struct R { char pad[0x2980]; struct { char p0[0xb]; s8 f; char p1[3]; } e[1]; }`.
Measured from the saved bests, judge scores 5 -> 0, 10 -> 0 and 5 -> 0 (decomp-repair `member_array_rows`).

Failed experiments that would otherwise be repeated (all stayed at 5): a cast pointer to an array of rows
`(*(s8 (*)[8][16])(D + 0x2980))[i][0xB]`, `((u8 (*)[16]) D)[i][K]` (`row_subscript`), a pointer to an array of
record structs indexed with `[i]`, a record pointer indexed with `[i]`; plus the hand attempts: operand swap, casts,
local pointer for the global, stride locals, `<< 4`, offset locals. A local holding the sum (`p = D + i*16`) scored
820 and a base local 410. Only an array member of a structure (the member reached through the cast base) changes the order.

Controlled on three functions of one game; the rule is a hypothesis about the expansion order, the measured effect is
not.
