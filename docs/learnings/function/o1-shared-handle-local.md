---
title: o1 shared handle local
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 179-instruction N64 function that saved every callee register before copying its parameter, so it was built with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The saved best was tuned under -O2 with fences (2044; 1425 under -O1).
It makes one effect call, then loops making another, each returning a 16-bit handle.

Source change: plain C with each local declared where it starts scored 325 (a sixth saved register and 8 more bytes
of frame). One `u16` local for both handles, instead of `u16 h = ...;` in the outer block and `u16 h2;` in the loop
body, was exact (0, the app's judge). The handle's `s32`/`u32` spellings were worse (500 to 820).

Failed: merging only the two slot locals (325); typing a single local per handle `s32` or `u32` (520 to 820).

Permuter: decomp-repair `initialized_local_merge` splits initialised declarations so the merge families can see the
pair. One function; the compiler's allocation reason was not read from its source (hypothesis).
