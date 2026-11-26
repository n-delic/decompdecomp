---
title: a global counter's old value: t = G++ rather than t = G; G = t + 1
kind: function
compiler: sn-n64-272-0001 -G8 -mips2 -O2
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 121-instruction N64 audio sequence starter in the 0x8008Fxxx library stretch. It takes a voice for the sequence and
for each of its channels, and returns the id it got from a global counter. The target's epilogue is `jr ra; nop`, so
the profile is `sn-n64-272-0001 -G8 -mips2 -O2` (SN GCC 2.8.1 always fills that slot). The m2c draft scored 2179 under
the default profile and 370 under this one. Its plain form, without a `volatile u64` scratch local, scored 415.

The source change, and the measured result before and after (scores, the judge used):

- m2c's `temp_v1 = D_8017B220; D_8017B220 = temp_v1 + 1;` written as `temp_v1 = D_8017B220++;`: 415 -> 50. From the
  two statements, the local is the load's destination, so the id sits in a saved register from the load on. From
  the post-increment, `expand_increment` loads into a temporary, stores the sum, and copies the old value into the
  local after the store: `lw $v1; addiu $v0,$v1,1; sw $v0; move $s5,$v1`, as the target has it.
- The voice (0x13C bytes) and the sequence (three `s32 *` channel arrays) typed as records, with
  `v = &D_8017B20C[func_8008F3D8(seq, i)]`. This removed the last register rows (`D_8017B20C` loaded into `$a0`), and
  the frame became the target's 0x38 without the scratch local.
- The chained stores written in the target's order (`v->x4 = v->x80 = seq->x24;` stores `x80` first): 0. The
  `compiler_experiments` trial was exact, and `suggest` applied it.

prove_chain `--families all` from the plain form stopped at 235, with scaffolding. decomp-repair `postinc_copy_merge`
(PR #1777) now offers the first edit.

Failed experiments that would otherwise be repeated:

`temp_v1 = D_8017B220; D_8017B220++;` compiles the same as the two-statement form (433). Writing `base + i * 4` in
either operand order changed nothing while the records were untyped.

What is controlled evidence and what is one function:

One function. Each step was measured with the app's judge on the same profile.
