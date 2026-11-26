---
title: nested call arg hoisted before stack stores
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

A 60-point N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) stored fields of an allocated record, the
last from an eleven-argument call whose last argument was `(g(8) + 0x26) & 0xFF`. With the nested call written in the
argument list, the `andi` was scheduled among the stack-argument stores, ahead of stores the target makes first.

Source change: `s32 t;` declared with the locals, `t = g(8) + 0x26;` as its own statement right before the call, the
argument `t & 0xFF`. Exact judge 60 -> 0. `u8 t` with `t & 0xFF` and `u8 t` with the mask dropped were exact too.

Family: decomp-repair `call_arg_result_hoist` (calls with five or more arguments; the argument is a call plus or minus
a constant, optionally masked; children s32, u8 kept mask, u8 dropped mask). Run on the pre-match source, all three
children score 0 with fast_judge. The scheduling explanation is a hypothesis.

Not covered: a nested call result used bare, or in another operator shape (multiplied, shifted), and a call with fewer
than five arguments.

One function.
