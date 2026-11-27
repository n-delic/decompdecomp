---
title: abs() gives bltzl with the negu in its slot at -O1, and the draft may test the sign as 0 > x
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 426-instruction N64 per-frame update (a 15-step history sum, then float state steps), gcc-2.7.2-kmc
`-G0 -mips3 -mgp32 -mfp32 -O1`; its own profile was `-O2`.

The target's `bltzl $v1; negu $v1, $v1` is the `abs()` builtin; `x < 0 ? -x : x` compiles to a plain branch. A saved
source spelled one test `if (0 > v) { v = -v; }`, which the `abs_idiom` rule did not read; with the reversed test it
takes the fence-free saved best from 2775 to 2610 (the app's judge).

The match itself (0) was a rewrite from the assembly: one record type for the object, `abs()`, `cond ? 1 : -1` and
`base + (cond ? -90.0f : 90.0f)` as conditional expressions, each range test with its own `else` store (jump.c merges
them), and the table read as a float member of a 0x64-byte record. No single repair reaches it from the saved best
(2790 under `-O1`): that one also read a local before assigning it and held a float in memory.

Controlled evidence: the rule's unit test. The scores are one function.
