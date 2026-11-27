---
title: Exchanging the factors of a final product moves which address is computed first
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 function of 496 instructions, gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`. A chained store
`rows[id].out = p->other = (40.0f - a) / 40.0f * rows[id].speed;` was at 310: only the final store pair differed (the
target keeps the record pointer in one register and a copy of the record offset, made in a branch delay slot, in another).

The source change, and the measured result before and after (scores, the judge used):

`rows[id].speed * ((40.0f - a) / 40.0f)`: 310 -> 0 (app judge). The emitted `mul.s` still has the quotient first; what
changes is that the member's address is expanded before the quotient. decomprepair `commute_factor`
(decomp-repair #1969) emits exactly this child.

Failed experiments that would otherwise be repeated:

All 544 library children of the saved source (before `commute_factor`) scored 535 or worse. Reading `p->speed` through
the pointer: 655. A separate `f32 *` for the store: 755. A copy of the index in a local: 1116.

What is controlled evidence and what is one function:

One function.
