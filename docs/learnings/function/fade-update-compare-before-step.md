---
title: fade update, the test of a stepped byte taken into a flag before the step
kind: function
compiler: SN64 GCC 2.8.1 -O2
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 37-instruction N64 object update: it reads an alpha byte, calls a movement helper, stores the byte minus one into
three fields, releases the object below 0x81 and otherwise applies two shifted velocities. Compiler `sn-n64-281`,
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The target after the call is `addiu $v0,$s0,-1;
move $s0,$v0; slti $v0,$v0,0x81; sb $s0` three times, `bnez $v0`.

The source change, and the measured result before and after (scores, the judge used):

The m2c form (`u8` local, `n - 1` repeated at every use and in the `if`) scored 325 with the app's judge run
standalone. This was exact (0, applied through a compiler trial):

    s32 n = p->alpha;            /* int, not u8 */
    tick(p);
    expired = n - 1 < 0x81;      /* before the step */
    n--;
    p->a = n; p->b = n; p->alpha = n;
    if (expired || p->stop) { ... }

decomp-repair `compare_before_step` offers it as one child.

Failed experiments that would otherwise be repeated:

Under the same profile: `n--; if (n < 0x81)` 430; `if (--n < 0x81)` ahead of the stores 580; the flag form with `u8 n`
325, with `u32 n` 200; `t = n - 1; n = t; t = t < 0x81;` 5. The function's earlier profile (SN64 GCC 2.7.2 build 0006)
reached 160 and writes the epilogue as `addiu $sp; jr $ra; nop`, where the target has the `addiu` in the delay slot;
KMC GCC 2.7.2 was 835. The profile and the source had to change together.

What is controlled evidence and what is one function:

One function. Why the compiler keeps the copy in this form was not checked in its source.
