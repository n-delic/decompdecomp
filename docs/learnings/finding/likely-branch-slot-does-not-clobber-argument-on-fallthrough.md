---
title: a branch-likely annuls its slot on the fall-through, so the argument register in it is still live
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: observation
date: 2026-11-25
---

What the function looked like: `lw $v0, 0x7C($s0); bnel $v0, $zero, L; addiu $a0, $zero, -0x14; jal f`: the slot writes `$a0`,
but a `bnel` runs its slot only when taken, so on the path that reaches `jal f` `$a0` is still the caller's argument (the `bnel`
vs `bnez` row is the hint that the callee takes it). m2c drafts the call `f()`.

The source change, and the measured result before and after (scores, the project judge): pass the parameter, `f(arg0)`.
`argument_passthrough_to_callees` counted the slot on both paths and never fired; with the slot on the taken path only (decomp-repair
0.5.1046) it fires on four more drafts: 1445 -> 1245, 1525 -> 1325, 830 -> 640, 1370 -> 1300 (first child; each a matched function).

Failed experiments that would otherwise be repeated: none.

What is controlled evidence and what is one function: four functions, judged on the draft with the one child.
