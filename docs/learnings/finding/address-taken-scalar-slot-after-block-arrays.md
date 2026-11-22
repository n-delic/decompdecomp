---
title: An address-taken scalar's frame slot comes after every array declared above it; declare the arrays in a block after the call
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-22
---

A 418-instruction N64 function (`gcc-2.7.2-kmc`, `-O2 -G0 -mips3 -mgp32 -mfp32`) with one out-parameter (`&status`) and
two 3-float arrays. Saved best 220: five `s` rows (the arrays at 0xa0/0xb0, the scalar at 0xc8, the target having the
scalar at 0xa8 and the arrays at 0xb0/0xc0) and the join guard of a `||` (see below); every other instruction equal.

Rule (read in gcc-2.7.2 `stmt.c`, `expand_decl`): a non-volatile scalar that is not addressable when declared gets a
pseudo register, and `put_var_into_stack` gives it a frame slot when `&scalar` is first expanded; an array or struct
gets its slot when its declaration's scope is entered. Frame slots are handed out in that order, lowest address first.
m2c declares every local at the top, so each array is allocated before any address-taken scalar. The target's order
(scalar first) means the arrays belonged to a block that opened after the call.

Fix: open a block after the statement that takes the address and declare the arrays in it (their whole use stays in it).
The order of the arrays inside the block matters (the first declared is the lowest): spB0 then spC0 was exact, the
reverse 23. The saved best had reached the same frame with `volatile s32 spA8` and a dummy `volatile s32 framePad[2]`
moved above it (215 -> kept 5 rows of guard); the block form needs no volatile on the scalar (the dummy 8-byte slot
stayed, the target has an unexplained 8-byte hole before the scalar).

The other half, same function: m2c's `(var = ptr != NULL, a) || (var = ptr != NULL, b)` followed by
`&& (var && (other != NULL))` makes the flag live in one register across both arms; the target recomputes
`sltu` at the join (in both delay slots). Writing `ptr != NULL && (other != NULL)` at the join took 220 -> 5.
`boolean_guard_operands` had this sink but only for `(var && other != NULL)` without parentheses.

Result: `boolean_guard_operands` (fixed to accept `(other != NULL)`) then `late_aggregate_scope` (new, decomp-repair)
reach 0 from the saved best in `prove_chain.py` (220 -> 5 -> 0). The two edits are separate steps; neither needs the other.

Failed: array-typed `s32` stand-ins for the scalar (`s32 x[3]`, struct) give the frame but not the delay slot of the
following `bne` (130): the volatile read keeps the store behind it; a plain read lets sched1 move it. Declaration order
of the top-of-function locals alone changed nothing (all 24 orders of four declarations).

Controlled evidence: one function. The allocation rule is from the compiler source; the exact layout was measured.
