---
title: An argument computed inline is suggested $a0; a carrier set twice takes $v0
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: observation
date: 2026-11-27
families: call_argument_carrier
---

What the function (or case) looked like and the exact compiler and flags:

An N64 game's 34-instruction allocator setup (KMC gcc 2.7.2, `-O1 -g`) aligns a global to 16, passes it to a call,
aligns the result and stores three values from it. Target: `lui $v0; lw $v0; addiu $v0,$v0,0xF; li $s0,-16; jal;
and $a0,$v0,$s0`. The saved best `temp = (f((G + 0xF) & ~0xF) + 0xF) & ~0xF;` was 20: the load and the add in `$a0`.

The source change, and the measured result before and after (scores, the judge used):

`a = (G + 0xF) & ~0xF; a = f(a) + 0xF; x = a & ~0xF;` and the stores from `x`: exact (20 -> 0, the app's judge).

Why, from the `.lreg`/`.greg` dumps: inline, combine folds the `and` into the `$a0` argument copy, so the add's pseudo
dies in an insn that sets `$a0` and local-alloc's `combine_regs` records `$a0` as its suggestion (the load is tied to
it). `a`, set twice and dying twice, is a global pseudo: nothing ties the add to `$a0`, and global.c gives `a` the `$v0`
it prefers from the call result. The matched twins (`x = ALIGN(G); x = ALIGN(f(x)); D = x;`) keep one `x` for
everything and still get `$v0`, but here `x` is read by two more stores: local-alloc first gives their sums `$v0`
while `x` is live, so global `x` conflicts with `$v0` and takes `$a0` (40). Hence the final mask into its own local.

Failed experiments that would otherwise be repeated:

The twins' form with the stores reading `x` (40); a separate local for the aligned argument, set once (20); a
carrier for the sum alone or the load alone (20); `c = f(c); v = (c + 0xF) & ~0xF;` (20); the mask in a local (225);
`~0xF & a` (20); an unused `s32 arg0` parameter (no change); the third store re-read from the globals (170-185);
`-O1`/`-O2` without `-g` (180, 1225).

What is controlled evidence and what is one function:

One function, judged in the app; the allocator reasoning is from the dumps and local-alloc.c/global.c of the
reconstructed 2.7.2 source. decomp-repair `call_argument_carrier` offers the rewrite; its first child of the saved
best was 0.
