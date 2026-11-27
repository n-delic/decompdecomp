---
title: m2c draft statement swap and halfword table
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 52-instruction N64 pickup initialiser that ends by setting a byte flag and a halfword table entry to 1, then
calling a sound routine. The m2c draft (1060) declared the table `extern char T[];`, so it wrote the entry with `sb`,
and wrote the flag first. Compiled with the profile above.

The source change, and the measured result before and after (scores, the judge used):

`extern u16 T[];` with `T[index] = 1;`, written above `flag = 1;` (or `flag = T[index] = 1;`): 1060 -> 0 on the
app's judge. Alone, on the draft: the swap 1010, the retype 1050. With the swap, the register that holds the
constant for the two last stores and the order of the earlier byte stores came right by themselves.

The families are `independent_statement_swap` and `scaled_byte_table_element`. The first refused every m2c draft
(a directive above the function, and `M2C_FIELD` read as a call); decomp-repair #2110 fixed that, and a greedy
`prove_chain.py` with the two families then goes 1060 -> 1010 -> 0 from the draft in 12 judged variants.

Failed experiments that would otherwise be repeated:

Holding the constant in a local (`u16 one = 1;`) with the flag written first reached 285 at best; so did a raw
`*(s16 *)(index * 2 + T)` store after the flag. A greedy chain that also takes `store_run_order` first drifts to 620.

What is controlled evidence and what is one function:

One function. The library change has its own regression tests.
