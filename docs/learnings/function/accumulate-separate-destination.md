---
title: accumulate separate destination
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: small N64 string-width loops (KMC gcc 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`) whose target adds a call result to a running sum with the call result first
(`addu $s2,$v0,$s2`). Every spelling of the accumulate (`acc += f(x)`, `acc = acc + f(x)`, `acc = f(x) + acc`) compiled to
the accumulator first (`addu $s2,$s2,$v0`): one differing row per accumulate.

The source change, and the measured result before and after (scores, the judge used): declare a temporary right after the
accumulator and write `temp = f(x) + acc; acc = temp;` (the declaration order mattered). Hypothesis from gcc's optabs
`expand_binop`: a commutative operation whose destination equals an operand puts that operand first; a separate
destination keeps the order written. Exact judge: one function 5 -> 0, a twin 5 -> 0, a third 20 -> 15, and 20 -> 0 once
m2c's `v = x << 0x10 ... return v >> 0x10;` scaffolding is also written as `return (s16) x;` with `v` deleted (the library's
`accumulate_separate_dest` offers the combined child; `shift_pair_return_cast` the cast alone).

Failed experiments that would otherwise be repeated: all three spellings of the accumulate without a temporary; the cast
return alone before the accumulate is fixed (no change in the rows). One remaining-row function with a global-first
address sum (`addu $v1,$v1,$v0` vs ours `$v0,$v1`) is not this family: no accumulate, no child of any family improved it.

What is controlled evidence and what is one function: four saved bests of one game, three improved as above; the cause
is a hypothesis read from gcc's source, the judge decided each result.
