---
title: o1 record slot stores with indexed reads
kind: function
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 99-instruction leaf that updates two bytes of one 100-byte record, under three tests, `gcc-2.7.2-kmc -G0 -mips3
-mgp32 -mfp32 -O1`. The target reads each byte by index (`lui; addu; lbu %lo`) but stores both through one address
register (`sb $v1,0($v0)` and `sb $v1,0xc($v0)`), except an arm that stores zeros (`sb $zero,%lo(table)($at)`).

The source change, and the measured result before and after (scores, the judge used):
Plain `table[i].a++; table[i].b++;` scored 965. Holding the tested byte in a local for the test and the update gave 645
(`record_slot_held_test`); writing the stores as `T *p = &table[i]; p->a = table[i].a + 1;` in every updating block,
with the zero stores left indexed, gave 0 (`record_slot_store_pointer`). Judged by the app.

Failed experiments that would otherwise be repeated:
`-O2` (2265 for the plain rewrite); the pointer in the zero-storing arm as well (735 together with the others).

What is controlled evidence and what is one function: one function. prove_chain reaches 0 from the plain rewrite in two
steps.
