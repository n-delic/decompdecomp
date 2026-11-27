---
title: At -O1, a clamp that reloads its value between the two range tests is written over-limit arm first
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 650-instruction N64 input handler with four
copies of one clamp of a byte field by a signed 8-bit step, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The target
tests `v + d < 0x80`, then loads the index and `v` again and sign-extends `d` again for the `< 0` test.

The source change, and the measured result before and after (scores, the judge used):

```c
if (v + d < 0x80) { if (v + d < 0) v = 0; else v = d + v; } else v = 0x7F;      /* 7660: the sum is reused */
if (v + d >= 0x80) v = 0x7F; else if (v + d < 0) v = 0; else v = d + v;         /* 0: reloaded, as the target */
```

Measured with the app's judge (isolated, then the app's own). decomp-repair's `else_if_arm_swap` already proposes the
second form from the first.

Failed experiments that would otherwise be repeated: a `do { } while (0)` around the inner `if`, a `goto` to the add
arm and a nested ternary all kept the sum in a register for the second test.

What is controlled evidence and what is one function: one function (four sites). That the second test sits at a label,
which ends the first CSE pass's block, is a hypothesis; it was not read in the compiler's dumps.
