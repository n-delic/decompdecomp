---
title: A copy loop's source address under -O1 needs its parts named in the loop body
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 61-instruction N64 routine: copy eight halfwords from a two-level record table (`table + 0x64 + a * 0x178 +
(id & 0xFF) * 0x12`) or from a pointer table, then store a terminator. The function's own profile was -O2; it is -O1
code (applied to this function only through a compiler trial).

The source change, and the measured result (the app's judge, under the -O1 profile): a source pointer set before the
loop gave the right instructions in another order and register. Naming each part inside the loop body, destination
first, gave the target's order, which fits loop.c moving the invariants in the body's order (a hypothesis):

    u16 *d = &dst[i]; int first = a * 0x178; int second = id & 0xFF; char *base = (char *) table + 0x64;
    *d = ((u16 *) (second * 0x12 + (first + (int) base)))[i];

The other steps from the saved best (2225): drop the narrowing casts on the loop counter, a `u16` parameter, and open
the two `do { } while (0)` wrappers. `prove_chain` reaches 0 with decomp-repair `loop_copy_address_stage`,
`loop_index_cast_strip`, `parameter_width` and `loop_goto_form`.

Failed experiments: the plain array-of-records subscript (820-880), the base cast before the subscript (670, the
table's `lui` comes first), one pointer variable holding base plus first term (430 or worse), and an offset local for
the first term only (65: the copied parameter and the first term swap `$v0`/`$v1`).

What is controlled evidence and what is one function: one function.
