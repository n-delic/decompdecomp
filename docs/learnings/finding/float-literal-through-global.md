---
title: A float constant m2c read through a global is the compiler's literal; write the literal
kind: finding
compiler: sn-n64-281 -G0 -mips2 -O2; sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

Three functions of an N64 game, built by SN GCC 2.8.1 (`sn-n64-281`; two with `-G0 -mips2 -O2`, one with the SN
defaults `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`): a 50-instruction compare-and-call function,
a 40-instruction spawner that offsets a copied position and an 18-instruction copier that does the same. Each m2c
draft reads a float constant through a global: `extern f32 D_8000930C;` and `if (temp_fs0 < D_8000930C)`, or
`... + D_8000123C`. The word at the symbol is the IEEE constant (0x43FA0000, 500.0f; 0x42C80000, 100.0f twice), and
each symbol is referred to by that one function alone. The target loads it as GCC loads a literal from its pool,
`lui $at, %hi(D)` / `lwc1 $fN, %lo(D)($at)` (the assembler's `l.s` macro builds the address in `$at`); a global read
is `lui $vN` / `lwc1 ... ($vN)` instead. So the saved bests kept a register row (scores 10 to 205) that no spelling of
the global closes.

The source change, and the measured result before and after (scores, the judge used):

The global's read replaced by the literal: `temp_fs0 < 500.0f`, `... + 100.0f`. The library family
`float_literal_global` (decomp-repair) does it when decompdecomp hands it the word (`app/float_literal_words.py` reads
it from the ROM, only for a symbol one function refers to, loaded through `$at`/`$gp`, and only when the word is a
plausible float). Proven with the skill's `prove_chain.py --families float_literal_global` and the app's standalone
judge (`fast_judge`), each function under its own profile:
- compare-and-call: m2c draft 10 -> 0 in one step; the saved best (10, the draft as autopilot tried it) -> 0.
- spawner: m2c draft 85 -> 0 in one step; the saved best (205 under its current profile) -> 0.
- copier: saved best 145 -> 0 in one step; from the m2c draft 670 -> 260 alone (the rest is the frame), then
  `unused_frame_local` -> 0; `--families all` picks `float_literal_global` first and reaches 0 in two steps.

Failed experiments that would otherwise be repeated:

Under KMC GCC 2.7.2 (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) the literal does not help: KMC builds a float
literal with `lui $at, 0x43fa` / `mtc1` (kmc-272-versus-sn-281-code-habits.md), not a pool load, and the matched C
itself scores 1115 under KMC. A `lwc1 ... ($at)` of a named word is a sign of a pool literal under SN GCC (and other
GCCs that use `l.s`). Earlier agents tried locals, casts and statement orders on the global (all 10): the register
comes from how the value is loaded, not from where it is used.

What is controlled evidence and what is one function:

Three functions of one game, each matched with the judge; the rewrite is the whole fix in two of them and the first of
two steps in the third. That `l.s` explains the `$at` load is read off the targets, not from the compiler's source: a
hypothesis. A literal shared by two functions of one file, or a literal under `-G8` loaded through `$gp`, are covered
by the family but not yet seen in a game.
