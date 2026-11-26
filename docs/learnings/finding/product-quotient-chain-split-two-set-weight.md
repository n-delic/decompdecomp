---
title: product quotient chain split two set weight
kind: finding
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp32
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 378-instruction N64 colour interpolator: two edge lengths and angle cosines/sines (`sqrtf` builtins with their call
fallbacks), then a weight pair `w = t * sin2 / len1; u = t * sin1 / len2;` and three channels
`r = ((c1 - c0) * u + (c2 - c0) * w + c0) * tint / 255.0;` from three vertex colours stored in stack arrays. SN64 GCC
2.8.1 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32` (not exact yet, so the function keeps its profile).

The source change, and the measured result before and after (scores, the judge used):

Isolated app judge-worker under `-mfp32`, each step from the one before:
- `w = t * sin2; w /= len1;` (u left as one statement): 3446 -> 3235. The target computes `t * sin2` first and puts
  `t * sin1` in t's register; with `w` set twice its product is no longer a single-set (birthing) insn for sched1.
- a move-one-line hill climb of the colour statements: 3220, a pair-swap pass 3215.
- each channel `r = e * tint; r /= 255.0;` (`g` as `g = e; g *= tint; g /= 255.0;`): 2756; uniform `r`-form 2775.
- hill climb again: 2581, 2577.
decomp-repair `compound_operand_split` now splits `*`/`/` chains at their last operator (it found every one of these
splits), and its combined child is applied by place (it garbled the source before).

Failed experiments that would otherwise be repeated:

- The colour statements in natural member order (c0, c1, c2): 4171-6495 in every t/w/u placement (a 600-point grid of
  t, w, u and the tint read at the block boundaries: 5710 best).
- `u` split as well (`u = t * sin1; u /= len2;`), alone or with `w`: 3451-5430.
- `t` split (`t = sin1 * cos2 + cos1 * sin2; t = lenl / t;`): 3860.
- Operand orders of the products (`sin2 * t`): no change.

What is controlled evidence and what is one function:

Each score is measured from its parent in the isolated judge. That the birthing boost decides it is a hypothesis from
the sched1 dump (`-dS`: the colour loads have priority 1-2, the weights' chain 11-23).
