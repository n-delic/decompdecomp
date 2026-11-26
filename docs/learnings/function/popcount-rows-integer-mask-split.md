---
title: bit-count over table rows exact at -O1 with the mask applied in a second statement
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 92-instruction N64 leaf counts the set bits of `table[row][j] & ~table[arg][j]` over eight words per entry, then
the bits of `table[row][0]`, and stores each entry's count. Its saved best (965) was an m2c draft with identical arms
and wrappers under `-O2`; 10 of the 12 nearest matched neighbours were `-O1`.

Measured with the app's judge, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`:

- Plain loops with `u8` counters, `u32 table[][8]`, one counter reused by the second bit count: 405.
- Dropping the `if (count != 0)` around the outer `for` (the `for` makes that test itself): 205 (`redundant_loop_guard`).
- `bits = table[row][j]; bits &= ~table[arg][j];` for the one-statement form: 0, recorded by the app through a
  `compiler_experiments` trial. `compound_operand_split` split float statements only; it now splits 32-bit integer
  `&`, `|` and `^` too (decomp-repair), and the two families chain from 405 to 0.

Failed (earlier attempts): `-O2`, `-O1` with scheduling passes on, and other compilers stopped at 2580 or worse.

One function.
