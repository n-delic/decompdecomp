---
title: A halfword tested twice is read through the pointer both times, not held in a u16 local
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 132-instruction N64 input-sequence routine: three blocks, each testing the pad's halfword (through a global pointer to
a pointer) against the next entry of a halfword table, stepping a counter, and on the `else` arm testing the halfword for
zero. Compiler and flags as above (per-function profile).

The source change, and the measured result before and after (scores, the judge used):

The symptom is `andi $v0, $a0, 0xffff` before the `bnel` of the `else if`, which a `u16 buttons` local never gives: its
second test reads the register as it is. Writing the read at both tests (`if (**gPad & table[i]) ... else if (**gPad)`)
emits it. A typed rewrite from the assembly: 1825 with the local, 235 with the reads inline, then 20 with the pointer read
as the first operand of `&` and 0 with two store orders. From the m2c-derived saved best, with library families only
(decomp-repair PR 2199), all by the app's judge standalone: 3128, 2988 (`condition_read_reread`), 2023
(`stride_element_read`: `*(s32 *)((i * 2) + bytes)` as an element of a `u16` table), 730 (`masked_copy_inline` on the
word parameter's `& 0xFF` copies), 0 (`guarded_increment_sink`, which refused an arm that later resets the counter).

Failed experiments that would otherwise be repeated:

Writing the byte table's read as `((u16 *)bytes)[i]` scored the same 630 as the byte-offset cast; only declaring the
table `u16` moved the table's `lui` before the counter's. Sinking the increment without removing the parameter copies was
worse alone (2023 to 2138). A `u8` parameter instead of `s32` with `& 0xFF` emitted the same code.

What is controlled evidence and what is one function:

All of it is one function. That the `andi` comes from a user variable being held in a promoted register, while the
unnamed halfword is not, is a hypothesis read from the output, not from the compiler's source.
