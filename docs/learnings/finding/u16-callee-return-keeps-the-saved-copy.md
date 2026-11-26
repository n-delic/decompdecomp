---
title: A u16 callee return keeps the compare on the saved copy
kind: finding
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 148-instruction N64 spawn routine, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. After a call the target does
`move $s1,$v0; addiu $v0,$s1,2; andi; sltiu` (a `(u16)(handle + 2) < 2` test of a handle); every spelling with an
`s32` prototype read `$v0` directly in the `addiu`.

The source change, and the measured result before and after (scores, the judge used):
Declare the callee as returning `u16` in the file's prototype (isolated judge 5 -> 0, then the app 0). The library's
`narrow_widening` makes that step by itself from the 5-point source. Other parts of the match: the three tables at
consecutive addresses written as one 0x7C-byte record array (`slot[id].flag[col] = 1`), an unsigned counter, and
`!(bit | (full = count >= 7))` for `sltiu; xori; or; bnez` instead of two branches.

Failed experiments that would otherwise be repeated:
Respelling the `(u16)(handle + 2)` test, an inline predicate, `handle == 0xFFFF || handle == 0xFFFE` and `s32 handle`
all compiled to the same instructions.

What is controlled evidence and what is one function:
One function; the rule that the callee's declared width decides it is a hypothesis about cse.
