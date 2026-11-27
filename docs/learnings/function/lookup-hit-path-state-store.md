---
title: A lookup's hit path stores the same state as its miss path (missing li/sw pair)
kind: function
compiler: gcc-2.7.2-kmc
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 103-instruction DMA block cache lookup of an N64 game, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The saved
best was adapted from a matched look-alike in another game and judged 200: the only difference was two missing
instructions, `addiu $v0, $zero, 2; sw $v0, 8($a2)`, before the early return of the block that was found.

The source change, and the measured result before and after (scores, the judge used):
`n->state = 2;` before `return n;`, mirroring the `freeBlock->state = 2;` the miss path already had: 200 -> 0 (the
app's judge). decomp-repair `sibling_return_store` offers it (and one child for `n->prev = 0`, judged 250).

Failed experiments that would otherwise be repeated:
`prove_chain` over every family of decomp-repair 0.6.258 (853 children of the saved best) found nothing below 200:
no rewrite of existing statements can add a store. A look-alike's source can lack a statement the target has; read
the differing rows' operands (an offset and a literal already stored elsewhere) before respelling anything.

What is controlled evidence and what is one function:
One function. The library test covers the rewrite on a small lookup.
