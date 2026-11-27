---
title: a stored one-byte global passed as address plus one: the address, then a pointer local
kind: function
compiler: KMC GCC 2.7.2 -O1 -g -G0 -mips3
evidence: verified
date: 2026-11-27
---

What the function looked like and the compiler:

An 80-instruction N64 menu setup: a one-byte global stored as zero and the call's first argument written D_X + 1; the draft folds it to the literal 1 (760).

The source change and measured result (app judge):

Pass &D_X + 1 (505), then hold the address in an s8 *p, store 0 through it and pass p + 1 (0). The second step is the existing symbol_address_local; decomp-repair stored_symbol_value_argument_address (0.6.295) adds the first: 760 -> 505 -> 0 in prove_chain.

Failed experiments:

Value form kept: 760; unsigned_switch was the only improving child from 760 (565) and led nowhere.

Controlled evidence:

One function.
