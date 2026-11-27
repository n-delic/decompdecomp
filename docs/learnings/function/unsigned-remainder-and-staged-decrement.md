---
title: N64 object update: m2c's unsigned pre-shifted remainder and staged decrement
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-27
---

A 92-instruction object update (fade channels by a timer, pick channels by a mode) from its machine draft, 1045 -> 0.
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, no profile change, no scaffolding.

Edits, each judged by the app's judge:

1. m2c wrote an unsigned `(x >> 2) % 12` (`multu` by 0xAAAAAAAB) as `h = x / 6; (x >> 2) - ((h >> 3) * 0xC)`.
   Writing the `%` alone: 1045 -> 230. Family: `shifted_dividend_remainder` (now reads the unsigned form).
2. Shifting the colour value in place (one local instead of `var >> 8` into a second): 230 -> 220.
   Family: `source_variable_reuse`.
3. A `goto` into the next `if` arm's shared store, written out in both arms. Family: `goto_forward_else`. It only pays
   with the others: alone the score does not move.
4. `t = X; n = t - 1; if ((t < 0) || (X = n, (n < 0)))` back to `if ((X < 0) || (--X < 0))`: the last 15 points.
   Family: `comma_decrement_test` (new).

With both library changes, `prove_chain.py --families` of those four families (`--combine 6`) reaches 0 from the saved
best: steps 3 and 4 above are found by its lookahead as one combined step. This is one function.
