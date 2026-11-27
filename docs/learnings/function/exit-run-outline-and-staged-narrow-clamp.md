---
title: An early-returning run written behind the function's end, and a narrow clamp staged through its local
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64)
evidence: verified
date: 2026-11-27
---

A 117-instruction N64 object update: a counter, a fading byte colour, four calls, two spawned effects, and a path for a
failed allocation. The app's judge: exact.

1. The failed-allocation path (`move the object; return;`) sits after the function's final store in the target. Written
   where m2c puts it (right after the `if` that jumps past it) it is laid out before the final store. Written as
   `goto L;` with `return; L: ...; return;` at the function's end: saved best's plain form 400 -> 10; the same structured
   rewrite 1155 -> 115. decomp-repair `exit_run_outline`.
2. The clamp `lbu $v1; addiu $v0,$v1,-1; move $v1,$v0; slti $v0,$v0,0x41; bnel; li $v1,0x40` is
   `v = p->g; t = v - 1; v = t; if (v < 0x41) v = 0x40;` with `s16 v` and `s16` or `u16 t`: 10 -> 0 on the plain form.
   decomp-repair `staged_narrow_clamp`.

Failed on the way (same compiler): the load folded into the subtraction (`t = p->g - 1; v = t;`) drops the `move`
whatever the widths; `s32`/`u8`/`s8`/`u16` clamped locals with the staged load leave the load in `$v0` or merge the two
values (5 to 405); inline min/max helpers and ternaries are worse (470 and up). With the m2c compare on the temporary
and a `u16` temporary the staged form scored 405: the test has to read the clamped local.

One function: that `exit_run_outline` and `staged_narrow_clamp` reach 0 from the saved best's plain form (prove_chain,
two steps). Not isolated: which compiler pass keeps the two values apart.
