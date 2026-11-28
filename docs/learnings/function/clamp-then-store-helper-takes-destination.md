---
title: a repeated dead-zone store written as a helper that takes the destination
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 796-instruction per-frame state routine. Its input loop reads both stick axes of every pad, cuts a dead zone out
of each (`v = x > 50 ? x - 50 : x < -50 ? x + 50 : 0`) and stores through a table of eight pointers copied from
rodata. The target loads each destination pointer before the first compare. It saves even float registers only
(`$f22`), so the per-function profile is `-mfp32` (under `-mfp64` the same source scored 25, five `$f21` rows).

The source change, and the measured result before and after (scores, the judge used):

A clean rewrite from the assembly (typed pad records, struct copies of the rodata initialisers, an else-if chain)
took the saved best from 16753 to 1840 and, with the run below, to 0 on the third candidate. Measured with the
isolated judge on the matched source, changing one thing at a time:

- one `static __inline__ void f(s32 *out, s32 x)` that computes and stores, flat `s32 *p[8]` indexed `i * 2` and
  `i * 2 + 1`: 0;
- the same helper over `s32 *p[4][2]`: 1310;
- no helper, destination in a pointer local before the branches: 155 flat, 1330 two-dimensional;
- no helper, store after the branches, flat: 1475;
- one counter for the input loop and the later player scan: 135.

decomp-repair `clamp_store_helper` (families `clamp_store_helper`, `flat_pair_member`, `clamp_store_helper_flat`)
writes the helper and the flat table: its children of the 1475, 155 and 1330 sources are exact. The shared counter
was already covered (`loop_counter_block_scope`, `loop_role_rename`: 135 -> 0).

Failed experiments that would otherwise be repeated:

A helper that returns the clamped value, assigned to `*p[...]` at the call site, loads the destination after the
branches. A conditional-expression macro stored straight into the destination puts a store in every arm.

What is controlled evidence and what is one function:

All numbers are one function. That the parameter copy of the destination gives the pointer a pseudo the pointer
local does not have is a hypothesis; the compiler's source is not public.
