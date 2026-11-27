---
title: two branch arms keep their identical store pairs when one store names a neighbour symbol
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 216-instruction menu cursor routine: a four-way `if` chain, each arm ending in two byte stores to adjacent globals (a direction pair). `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`.

Written plainly the compiler merged the first arm's two stores into the third arm's (810). Spelling the first arm's store of the second byte as `(&D_x22)[1] = -1;` instead of `D_x23 = -1;` kept both: 810 -> 0 (app judge). Same linked bytes. Repair `neighbour_store_spelling` offers it.

`D_x22 = arg0;` in that arm (the argument is known to be 0 there) also stopped the merge (5: `sb $s0` for `sb $zero`); `>> 8`, `& 0xFF00`, `* 0` and a `switch` did not (810, or 3005 for the switch).

One function. That jump2's cross-jumping is what merges them is read from the output, a hypothesis.
