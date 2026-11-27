---
title: asset loader string copy and shared global
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 212-instruction loader that picks one of four file names, reads the asset index by DMA and then the asset. Exact by
the app's judge. The matched sibling loader (75% alike) adapted by hand: 2010, then 530, then 0. The name was
`strcpy(name, "....")` in each arm, `else if (a == 3)` and `else if (a == 4)` written apart (`a == 3 || a == 4` folds
into a range test), and the heap pointer held in the first buffer's variable: `buffer = heap;
table = ROUND(buffer + 0x7D00); buffer = ROUND(buffer);`.

From m2c's draft (8170), every step through the library, same judge:

| step | score |
|---|---|
| `symbol_stack_block_copy` (word/half/byte copies into stack words become one `__builtin_memcpy`) | 3990 |
| `shared_global_round` | 3600 |
| `or_arm_split` | 2855 |
| `endian_word_load_early` (with the sum taken from a call argument) | 26 |
| `stack_object_size` | 0 |

`__builtin_memcpy(buffer, &D, 11)` from the symbol gives the same block move as the string literal, so the draft needs
no data. One function: that gcc 2.8.1 wants these forms is observed, not read in its source.

Found on the way: the app's draft pass offered only the last child of an m2c draft's written-out spelling (an
indentation slip), and `stack_locals_one_array` raised `KeyError` on an array only passed by name.
