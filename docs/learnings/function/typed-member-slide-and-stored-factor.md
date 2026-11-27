---
title: typed member slide and stored factor
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An 88-instruction N64 entity placer: three halfword scales stored as one constant, a speed from a size table, a
`while` loop that steps the entity until it touches nothing (a `u16` try counter, a `break` after too many), then a
reset block. SN GCC 2.8.1, the flags above. Written by hand with a typed record (`Ent *self`).

The source change, and the measured result before and after (scores, the judge used):

The app's judge. The typed rewrite scored 2255.

1. `size = table[self->type] * 0x6000;` as `* self->scale`, the member just stored with that value: the target has
   `li; mult; mflo`, the literal factor gives shifts and adds. 2255 to 700. decomp-repair `stored_constant_operand`.
2. `self->type = Next[self->slot];` above the byte store, the chain of halfword stores and the two word clears before
   it. 700 to 60. decomp-repair `typed_member_slide`.
3. `tries++;` below the two member updates and the call, not above them. 60 to 0. `typed_member_slide`.

The library's best children reach 0 from the 2255 source in those three steps. On the m2c draft (3197),
`stored_constant_operand` alone scores 2617.

Failed experiments that would otherwise be repeated:

`tries++` between the two member updates, or `if (++tries > 1000)`, did not match (60 and 305). Before these modules,
`statement_slide` and `table_read_statement_slide` offered neither move: they read `M2C_FIELD` cells and record casts,
not `p->member`, `n++` or a chain.

What is controlled evidence and what is one function:

One function. That the moved stores do not alias is a hypothesis; the judge decides. The reason the order of
`tries++` matters was not read from the compiler's dumps.
