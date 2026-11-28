---
title: A variable round-up written as steps keeps its chain in one register
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 22-instruction N64 bump allocator (a low and a high end 8 bytes apart in one object, aligned to `1 << align`). Its
best after 66 attempts was 1155 under KMC GCC 2.7.2; the function was built with SN64 GCC 2.8.1.

Exact source, with the app's judge:

- the two ends as fields (or elements 0 and 2) of one object, not two scalar externs (two externs: 500);
- `u8 align`, the size rounded in the parameter itself;
- `low += (1 << align) - 1; low >>= align; low <<= align;` and `high = high >> align << align;`.

`low = (low - 1 + (1 << align)) >> align << align;` scored 265 with everything else equal. Splitting only the
decrement (`low -= 1;`) scored 60 with registers left over: `low` then dies twice and goes to global allocation
(read from the `-dl -dg` dumps). The order of the two loads against the arithmetic made no difference.

decomp-repair `align_up_in_steps` writes the stepped form (and `X = A;` first when the destination is not `A`); from
the m2c draft it took 1400 -> 845 as its first step. The shared-object step from the draft's two symbols is still
not written by a family.

The saved best this function had also stored through a pointer one line above its assignment. The statement swap of
the simple rewrites made it (96 of 500 seeds on the parent); decomp-repair no longer swaps a first assignment below
its use.

One function: the register explanation is a hypothesis beyond the allocation dump.
