---
title: n64 o1 record initialiser carried offset
kind: function
compiler: gcc 2.7.2 KMC -O1
evidence: observation
date: 2026-11-26
---

A 97-instruction N64 initialiser: a loop over four 0x70-byte records, an inner loop copying 16 halfwords from another
table, then eighteen word stores. Project profile `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`; the function is `-O1`.

Plain C is exact under `-O1`: one record array (`u16`, two `u8`, `u16`, a `u16[16]` array, then words), two `for`
loops. Saved best was 1375 (1525 under -O1), flat byte externs with an offset temporary. From m2c's draft (5200):
`indexed_symbol_records` 820, then `goto_backward_loop` 0.

What the library lacked (decomprepair 0.6.162): m2c carries the inner byte offset in a temporary
(`off = 0 * 2; ... off = i * 2;`) and reads the source table through it, so the record rewrite declined. A member
array also has to be a plain array: `struct { s16 m0; } m6[17]` left the target's base register unmade (655).

Failed: `local_split_new` renamed `off = 0 * 2` apart from the use a loop reached round its back edge, a child that
read another value (fixed in the same release).

One function; the `-O1` profile was confirmed by the app's judge.
