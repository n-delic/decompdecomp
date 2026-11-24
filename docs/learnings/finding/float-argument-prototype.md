---
title: float argument prototype
kind: finding
compiler: gcc 2.7.2 sn-n64-281 (-G0 -mips3 -O2 -mfp64), o32
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: two small N64 functions (39 and 52
instructions) built with the sn-n64 profile `-G0 -mips3 -O2 -mfp64` (no `-mgp32`/`-mabi=32` in the flags). The target
builds round floats with `lui $aN, hi` (+ `ori`) into `$a1`-`$a3` before a `jal`; m2c wrote them as integer arguments of
an `M2C_UNK`/`s32` parameter (`0x4542B000`, `0x41A00000`).

The source change, and the measured result before and after (scores, the judge used): the callee's prototype takes `f32`
there and every call passes the float literal (`3115.0f`, `20.0f`): 60 -> 0 (first function: five integer-looking
arguments became floats, with `addiu $a0` scheduled after the `lui`/`ori` pairs as in the target) and 120 -> 0 (second:
`f32 g(f32, f32, M2C_UNK)` became `f32 g(f32, f32, f32)` with `20.0f`; the literal alone with the old prototype scored
260, since it is converted by value). decomp-repair `float_argument_prototype` finds both (prove_chain, exact judge).

Failed experiments that would otherwise be repeated: `float_argument_bits` already existed but needs `-mgp32`/`-mabi=32`
in the flags (this profile has neither) and skips any callee whose first parameter is a float (the o32 third argument is
in `$a2` whatever the first two are). `prove_chain --families all --combine 10 --children 400 --lookahead 3` found nothing
from either start.

What is controlled evidence and what is one function: two functions of one game, so one observation; the gate is the
target's own `lui`/`ori` into the argument's register, not the flags.
