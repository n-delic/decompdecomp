---
title: draft call short of copied stack arguments
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:
A 37-instruction N64 function that calls a matrix routine in either arm of an `if`, passing an output matrix and seven
floats read from three pointer parameters. The target copies the last four with `lwc1; swc1` to `0x10($sp)` onwards and
loads the first three into `$a1`-`$a3` with `lw`. The machine draft, with no prototype for the callee, passed only the
output and three `s32` words.

The source change, and the measured result before and after (scores, the judge used):
All seven values passed, the callee declared with `f32` parameters: 1614 to 0 in the app's judge, first try by hand.
decomprepair `loaded_stack_arguments` (RetroReversing/decomp-repair #2253) makes the same edit from the draft: 1614 to 0
in one step, scored with `prove_chain.py` from the library branch.

Failed experiments that would otherwise be repeated:
`prove_chain.py --families all` on the library before that change offered no call-argument child and stayed at 1614
within a 40-variant cap.

What is controlled evidence and what is one function:
One function. The library's tests use synthetic assembly; they check the generated C, not a compiler.
