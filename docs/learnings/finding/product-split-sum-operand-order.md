---
title: product split sum operand order
kind: finding
compiler: KMC GCC 2.7.2 (gcc-2.7.2-kmc), -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: a 20-instruction setter on an N64 game
(KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) storing `13 * ((a - 0x12) / 10) + 0x12` into two globals. The m2c draft
(`(((a - 0x12) / 10) * 0xD) + 0x12`) scored 5: one row, the final `addu`, had the quotient and the product in the
opposite order to the target (`addu $a1,$v0,$v1` against the target's `addu $a1,$v1,$v0`).

The source change, and the measured result before and after (scores, the judge used): writing the product by a constant
N as the sum `e + e * (N - 1)`, `((a - 0x12) / 10) + (((a - 0x12) / 10) * 0xC) + 0x12`, scored 0 (the app's isolated
judge, compiler profile unchanged). The decomp-repair family `product_split_sum` (v0.5.1000) proposes this
from the draft; `prove_chain.py --families product_split_sum` reached 0 in one step, 5 -> 0 (2 children). Before it,
`--families all` stalled at 5 with 65 children. Hypothesis, not confirmed in the compiler's source: the multiply
expander and the plus tree keep the operand order the tree gave them, so the quotient-first sum picks the target's order.

Failed experiments that would otherwise be repeated: caching the quotient in a local (`q = (a - 0x12) / 10;
q + q * 0xC + 0x12`) scored 285: the expression has to be recomputed in each term, so the family only duplicates
expressions that are free of calls, side effects, volatile names and floats (3x and x + 2x round differently).

What is controlled evidence and what is one function: one function, a controlled reproduction (the draft with the one
edit undone, recovered by the family). False-positive load without judging: over the 1073 matched functions of one
project, 65 sources (6%) have a site and the family offers 191 children in all, at most 11 per function. Whether it
helps on other functions is unmeasured.
