---
title: cse2 merges sums across do-while fences
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-23
---

A sum the target computes twice (`addu $v0,$a2,$s1` for two loads, later `addu $a1,$s1,$a2; addiu $a1,$a1,0x9c` for a call
argument, with `$s1` and `$a2` the same registers throughout) is one sum, with one register, when the source's two sums are
in one cse extended block. cse.c (`cse_end_of_basic_block`) stops the extended block at a `CODE_LABEL`, and at a
`NOTE_INSN_LOOP_END` only while `after_loop` is 0; the first cse pass runs with it 0 and the second (`-frerun-cse-after-loop`, on at
`-O2`) with it 1.

Measured on a reduced copy of the shape (a loop-free function, a record pointer `r`, a byte index `s`, `P = r + s` loaded through twice, a
conditional, then `call(sp, (u32) s + (u32) r + 0x9c)`), and in the full function, by `-ds` (cse), `-dL` (loop) and `-dt` (cse2) dumps:

- a `do { stores } while (0);` between the loads and the call: the first pass leaves `r + s` twice (insns `119 = 76 + 73` and
  `120 = 119 + 156`), the second pass turns the second into `P + 156`. The fence is no barrier. Its `LOOP_END` also stops the
  scheduler from moving the sum up among the earlier stores, so the rows then differ in order.
- a label that is only the target of a branch around a block (`if (c) goto lab; x = 1; lab:`) is followed by cse (the extended block
  goes around it, `invalidate_skipped_block` kills what the skipped block sets): the sums merge.
- a two-armed statement before the call (`if (a) sp[2] = 1; else sp[2] = 2;`) puts a join label with two uses in front of the
  call: neither pass follows it, and the compiled code is `addu $5,$5,$7` (the integer cast order of the source: the index first) followed by
  `+ 156`, exactly the target's pair of sums.
- `do { } while (0);` whose body is empty is deleted by the first jump pass, and a loop-notes pair around code does not survive
  to the second cse as a barrier.

So a target that keeps two equal sums apart has, between them, a join label that two jumps reach (a two-armed statement
whose arms the final jump pass cross-jumps into one, or an `if`/`else` the original source had); a fence or a label
that a single branch jumps around does not. Not tried on the function that prompted this (held by another session at 220):
which two-armed statement the original had. Hypothesis for the function, not confirmed.
