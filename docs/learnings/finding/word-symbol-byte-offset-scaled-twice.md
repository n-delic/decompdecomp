---
title: A byte offset on a symbol declared as one word is scaled by four again
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-27
---

m2c declares a table it saw through one address as `extern s32 X;` and writes `*((&X) + off)` or `off + (&X)`, with `off`
already a byte offset (index times record size). C multiplies by four again. The tell in the diff: the last shift of the
stride is `sll v0,v0,2` in the target and `sll v0,v0,4` (or an extra `sll`) in ours.

Writing the sum as bytes (`*(s32 *)((u8 *)&X + off)`) on a 176-instruction object updater with two such symbols: 155 ->
30 on a search state, 430 -> 280 on its saved best, all under `-O1`. decomp-repair `word_symbol_byte_offset` makes it.

On the same function a rewrite with a record array (0x64-byte records), a struct pointer and a signed `s16` countdown
(`lhu`, then `sll 16` before the zero test) under `-O1` was exact; the saved best (1355) was under `-O2`.
