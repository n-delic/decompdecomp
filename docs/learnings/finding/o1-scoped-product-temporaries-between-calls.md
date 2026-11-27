---
title: A product computed between two calls is its own block-local temporary at -O1
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 222-instruction orbit-camera setup (`a * d - b * c * d + base`, `a`, `b`, `c` being sine calls). The target multiplies
the first call's result by `d` before the next call. Written as one expression GCC makes every call first. A temporary
for the first product gives that order, but one temporary reused for all four products is a global pseudo and takes the
last float register (895 to 520); one scoped temporary per product, each set once, gave the target's registers
(285).

Also on this function: constant angles written as `angle * 3.1415927f / 180.0f` with `angle = 40.0f` are folded at their
first use and then held in a register (895 to 520), where the folded literal written twice is loaded twice; and
`x = s.x = (value);` puts the struct's address before the value's load (270 to 0, decomp-repair
`chained_assignment_split` finds this one).

One function. The allocator explanation is a hypothesis from the `-dg` table.
