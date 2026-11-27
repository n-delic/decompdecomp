---
title: m2c loses stack arguments at a shared call tail
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-27
---

An N64 function of 332 instructions that calls one 18-argument function, eight times in a row and then once per case of a
nine-case switch (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`). Each arm stores the first stack arguments, jumps into
the stores of the rest and shares one `jal`. m2c reads that call once: the draft called it with ten arguments, mostly
zero, and no arm kept its callback pointer. The compiler was merging the tails itself, so the source is a full call in
every case.

`m2c_call_tails.write_out` copies the tail (the jump's delay slot, the straight-line code through the `jal` and its
slot, then a jump past it) into every block that jumps into it with stack arguments half stored. The draft then has all
nine calls complete.

Measured with the isolated judge: the draft with the arms written out scored 2040, every difference a float argument
that m2c, with no prototype, wrote as its bit pattern (`0xC4188000`). With a prototype typing those three parameters
`f32` and the constants as float literals the same draft scored 0. A hand rewrite from the assembly with that
prototype also scored 0 on its first compile.

Still open: nothing types a stack argument `f32` from the target's `swc1` when the callee has no prototype.
