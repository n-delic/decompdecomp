---
title: two key lookup or arms result local
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 119-instruction lookup returning `u8` from two byte globals (an `if` tree on the first, an `if` chain on the second
in each arm), gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1` (project default `-O2`).

Two changes made it exact:

- One arm of the tree branches on each of `b == 0` and `b == 0x17` and reloads `b` between them, while the other arms
  have GCC's fold (`sltiu; xori; sltiu; or; beqz`). That arm was two arms with one body (`if (b == 0) r = 0; else if
  (b == 0x17) r = 0;`): 960 -> 0. decomp-repair `or_arm_split`.
- Each leaf assigns a `u8 r` that is returned once. `return` in every leaf lets the `b == 1` test branch straight to the
  epilogue with the compare's constant as the result (`beq` where the target has `bne; j; li`): 460 -> 0. decomp-repair
  `returns_to_result_local`.

From the saved best of 340 and more earlier attempts (1280 under `-O1`) the library's chain reached 500 with the first
change; the rest is that source's casts and its `s32` return of `r & 0xFF` (500 for that spelling alone).

Failed experiments: reordering the groups and `switch` forms (earlier sessions' notes).

Controlled: prove_chain with the exact judge on this one function.
