---
title: display list direct cursor on pointer argument
kind: function
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-27
---

A 170-instruction display-list builder (N64, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`; the prologue saves every
callee register before it copies a parameter) that takes the cursor by address: a pipe sync, a matrix load, mode
commands, vertices and one triangle.

The source that matched: `Gfx **gfx`, each command `{ Gfx *_g = (Gfx *)((*gfx)++); _g->words.w0 = ...; _g->words.w1 = ...; }`
(the SDK macros on `(*gfx)++`), a `u16` index parameter, records of 100 and 0x8F0 bytes declared as arrays of structs and
the matrices as `Mtx [][4]`. First try, score 0, against a saved best of 1605 under `-O2`.

Measured on the m2c-shaped sources with the library (isolated judge, `-O1`): `gbi_macro_param_cursor` wrote nothing
because a word stored through a `(s32 **)` cast was not read as a store; with that fixed its block-local copy
(`Gfx *gfx = *arg0`) judged 3940 and the same macros on `(*(Gfx **) arg0)++` judged 580. Then
`argument_weight_scope`, `integer_pointer_sum`, `word_symbol_byte_offset` and `float_word_load_retype` reach 225-425;
the rest is the callee prototypes the project header declares and one commutative address sum.

Failed: `prove_chain` from the saved best stalled at 415 before the family fix.

Controlled: the block-copy against direct-cursor scores. The rest is one function.
