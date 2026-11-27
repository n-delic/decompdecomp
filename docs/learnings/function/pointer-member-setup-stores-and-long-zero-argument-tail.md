---
title: a setup of fields through table[i].p after an 18-argument call: stale frame, store order and widths
kind: function
compiler: KMC GCC 2.7.2 -O1 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-27
---

A 160-instruction setup function: KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O1` (exact; under `-O2` the same clean C
scored 1875, with the index not reloaded after the `u16` global store). Two calls of an 18-argument callee, mostly zeros,
and constant stores through `table[i].p` (100-byte records), reloading the index after each byte store.

Clean C (`table[i].p[6] = 0; *(s16 *)(table[i].p + 2) = 0x14;` in offset order, prototype with all 18 arguments) was
exact at once. From the old saved best (3298, m2c's two identical `if`/`else` arms, 15 and 16 arguments, a `(float) 0`)
the library reaches 0 in three steps: `scaffold_strip` (637), `stack_argument_tail` (226), `indexed_record_stores`
(0: `(table + i)->p` and `(*(&table[i])).p` as `table[i].p`, which drops 8 bytes of unused frame; offset order; `sb`
for the first byte; alone they scored 220, 246 and 26).

`stack_argument_tail` had offered nothing: the first call's block holds the prologue saves (`sw $s0, 0x48($sp)`), which
it counted as argument words.

Observation on one function.
