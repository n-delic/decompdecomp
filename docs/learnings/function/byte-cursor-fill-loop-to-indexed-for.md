---
title: A fill of two buffers m2c wrote with byte cursors is an indexed for loop with chained stores
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-28
---

A 23-instruction N64 function stores 0xFFFF into one halfword of every eight bytes of two buffers, from a large fixed
offset, `count` times (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). The target has the
offset (`lui; ori`), the constant (`ori $t0, $zero, 0xffff`) and a copy of the bound (`move $t1, $v0`) ahead of the
loop, and steps two pointers the loop pass made. The saved best was m2c's guarded `do`/`while` with two `void *`
cursors and staged store addresses.

Measured with the app's judge (isolated, then recorded by the app):

- saved best (byte cursors, `s16 *` stores): 580
- `for (i = 0; i < count; i++)` indexing both bases as `u16`, two statements, first buffer first: 310
  (the two bases are loaded in the other order)
- the same, statements exchanged: 270
- chain `first[k] = second[k] = 0xFFFF;`: 40
- chain `second[k] = first[k] = 0xFFFF;`: 0
- a local `u32 colour = 0xFFFF;` as the stored value: 735

The bases may stay `void *` globals read inside the loop as `((u16 *) base)[i * 4 + K]`, with the bound read as the
guard read it (`*(s32 *) &count`): exact too.

Failed experiments: the `for` loop with `*(s16 *)((s8 *) base + i * 8 + K)` scored 1335 (the bases and the bound are
reloaded every pass, and the constant becomes -1); `u16 *` byte casts scored the same; a `do`/`while` over local
base copies indexed by the counter scored 585.

Library families (decomp-repair): `byte_cursor_fill_loop` goes from the saved best to 0 in one step;
`constant_chain_swap` takes the 40 chain to 0; `constant_store_chain` already took the 310 statements to 0.

One function; why the byte-address spelling keeps the loads inside the loop is not confirmed in the compiler's source.
