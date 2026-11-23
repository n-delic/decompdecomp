---
title: KMC GCC 2.7.2 and SN GCC 2.8.1 code told apart by three habits
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2; sn-n64-281 -G0 -mips2 -O2
evidence: controlled
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

An N64 game built with KMC GCC 2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2`) had eight near-matches stuck at score 10.
They were SN GCC 2.8.1 code (`sn-n64-281 -G0 -mips2 -O2`). The same small probe compiled by both compilers shows
three habits that differ, and they are the habits the targets showed:

- Zero arguments: `k(a, 0, 0, 0)`. KMC writes `move $a1,$zero; move $a2,$zero; move $a3,$zero`. SN writes
  `move $a1,$zero; move $a2,$a1; move $a3,$a1`.
- Float literals: `x * 1.5f + 0.25f`. KMC writes `lui $at,0x3fc0; mtc1 $at,$f0` (plus a `nop`). SN writes
  `lui $at,%hi(lit); lwc1 $f0,%lo(lit)($at)`.
- Two globals: `q_table[1] + q_word`. KMC writes `lui $v1; lw $v1,%lo()($v1); lui $v0; lw $v0,%lo()($v0)`, loading
  through its own address register. SN groups the two `lui`s and loads into other registers:
  `lui $v0; lui $v1; lw $a0,%lo()($v0); lw $v0,%lo()($v1)`. A single global looks the same in both
  (`lui $v0; lw $v0`), so it says nothing.

SN's own default flags (`-mips3 ... -mfp64`) show the same three habits as `-mips2`.

The source change, and the measured result before and after (scores, the judge used):

There was no source change. Under the right profile, the saved C of those functions went from 10 to 0, or to one
trivial edit, with the linked judge. `compiler_fingerprints` now reads these three habits, and
`profile_detection` scores a near-match's saved C under every installed profile. When we ran detection on a copy of
another KMC N64 game, a 35-instruction function whose saved best was 5 under KMC was exact as it stood under
`gcc-2.8.1-pm-sn-as -G0 -mips3 -mgp32 -mfp32 -O2`.

Failed experiments that would otherwise be repeated:

Earlier agents spent hours on source spellings under KMC. No spelling closes these gaps, because the code comes from
a different compiler. Once two of these habits appear in a near-match's target, score its saved C under the other
GCC profiles first.

What is controlled evidence and what is one function:

The probe output is controlled: the same source was compiled by both real compilers, and
`tests/test_profile_detection.py` checks it when both compilers are installed. A target that shows these habits is
only evidence about the compiler. The judge's exact match is what proves it. Small functions show these habits by
chance: a 6-instruction and a 9-instruction function with two of the habits each stayed at 10 under every profile.
