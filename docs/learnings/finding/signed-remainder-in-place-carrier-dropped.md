---
title: A signed remainder m2c wrote as a sign-bias block is % in place, with its sum carrier gone
kind: finding
compiler: sn-n64-272-0001 -G0 -mips3 -O1 -mgas -meb -mhard-float
evidence: observation
date: 2026-11-27
---

What the function looked like: an 18-instruction N64 leaf that scans a byte table for the next non-zero slot,
wrapping modulo 8 (SN GCC 2.7.2, `-G0 -mips3 -O1 -mgas -meb -mhard-float`). The target computes the remainder with
`bgez`/`addiu $v0, $a0, 8` (cse folded `(i + 1) + 7` into `i + 8`), `sra`, `sll`, `subu`, all into `$a0`.

The source change: m2c's draft (`var_v0 = var_v1; if (var_v1 < 0) { var_v0 = var_a0 + 8; } var_a0 = var_v1 -
((var_v0 >> 3) * 8); var_v1 = var_a0 + 1;` in a `do`/`while`, with `var_v1 = var_a0 + 1;` before the loop) was 710;
`var_a0 = (var_a0 + 1) % 8;` with the carrier `var_v1` gone was exact (0). The whole function is
`while (D[i] == 0) { i = (i + 1) % 8; } return i;` (also 0, as is the `if`/`do` form).

Failed experiments: `var_a0 = var_v1 % 8;` keeping the carrier: 850. The automatic search stopped at 15 after 6,784
trials (a saved best that stores the quotient in `var_a0` first); none of signed_division, division_in_place,
doubled_division_sign or the sign-bias families fires, because the bias is written against the index, not the
dividend.

Evidence: one function, measured with the app's judge. decomp-repair `remainder_in_place` (0.6.292) offers both
children from the draft.
