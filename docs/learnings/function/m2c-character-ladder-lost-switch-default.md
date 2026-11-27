---
title: m2c character ladder lost switch default
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

An 89-instruction N64 text converter: a loop over bytes, a ladder of range tests and a `switch` with a jump table, under
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

m2c prints the switch's default as a `default:` label in the `else` beside the switch. `m2c_types._stray_labels` dropped
the label, so the draft did nothing for an unmatched value. `_default_in_else` now copies the block into the switch.

Measured on the exact judge from the draft (7170 under this profile): `default:` restored alone 5315. The match (0) needed
that and four spellings together (decomp-repair `m2c_ladder_cleanup`, `m2c_ladder_combined`): the early `goto` into a case
written as an arm, byte cursors stepped beside the counter written as `base[i]`, the `if` around the switch turned round,
and `(u32) ((c - K) & 0xFF) < N` written as `c >= K && c <= K + N - 1`. With the other edits in, leaving out one of these
gave 1020 (the goto), 1235 (the turned `if`), 230 (the range), 5265 (the default). The `do { } while` can stay.

Failed experiments: `default: goto <label in the else>;` instead of a copy is 7245 (the compiler does not merge it into
the same layout). A greedy chain of the older families stopped at 6530 after 90 judged variants; its first step
(`goto_arm_tail_inline`) copied a `break;` out of its switch into the enclosing loop, since fixed.

One function; the tests are controlled.
