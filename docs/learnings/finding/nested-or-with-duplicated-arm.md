---
title: An || the draft split into nested ifs with one arm twice or a jump past it
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: exact-match
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 74-instruction N64 function that picks two
signed halfwords from a random number, SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.
The saved best (the m2c draft, 860) differed only in branch layout: `bnel`/`j` where the target had `beq`/`nop`/`j`,
and the reverse in the other arm. m2c had written one `if (a || b) T else S` two different ways:
`if (!a) { if (b != 2) { S; goto end; } } T` in one arm (the operands staged in locals first) and
`if (!a) { if (b != 2) S else T } else T` in another.

The source change, and the measured result before and after (scores, the judge used): both written as
`if (a || (b == 2)) T else S`, app judge, 860 -> 0. `decomprepair.dup_arm_or_merge` offers it as its first child
(every site in one), measured with the standalone judge from the same saved best: 0. A plain typed rewrite with the
same `||` in both arms was also 0 on the first try.

Failed experiments that would otherwise be repeated: only the first site as `||` scores 1460, only the second 1200,
the first site written in the nested form of the second stays 860. The `&&` spelling (`dup_arm_and_merge`) is 860.

What is controlled evidence and what is one function: one function. That jump.c's treatment of two source copies of
`T` is what changes the branches is a hypothesis, not read in the compiler's source.
