---
title: slot list record array masked copies
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 142-instruction walk of a linked list of 100-byte slots (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`; the project's
default `-O2` scored 3070 on the same source). The saved best was a search-shaped `-O2` source at 4365.

The source change, measured with the app's judge:

- Plain C under `-O1`: one record array for the slots, a `u16` index, `do { } while` and the three float
  interpolations written out. Exact on the first compile.
- From the m2c draft (6310 under `-O1`), the library now reaches it: `indexed_symbol_records` 1860 (the record starts
  at the aligned word before the lowest byte the code reads), `masked_copy_inline` 1420 (the `x = idx & 0xFFFF;`
  locals written as `idx`), `goto_backward_loop` 0.

Failed experiments that would otherwise be repeated: more than twenty earlier attempts under `-O2` with separate
symbols and byte offsets; the target's per-use chain walk and unshared index are what `-O1` writes.

What is controlled evidence and what is one function: one function; the two rewrites have unit tests in
decomp-repair.
