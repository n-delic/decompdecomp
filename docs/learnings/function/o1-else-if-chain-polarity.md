---
title: o1 else if chain polarity
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 169-instruction N64 function (a `switch` on a byte state that picks an animation number, then a three-way choice),
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (saved best 2085, tuned under -O2). Plain C under -O1 scored 1025.

Source changes, with the app's judge:
- an explicit `case 10:` beside `default:`, placed last, matched the jump table's range (1025 -> 725);
- `if (anim < 0) switch (anim)` inside `if (speed < K && anim < 0)`: the target tests the sign again before the
  switch's compare tree (725 -> 365, `conjunct_reguard`);
- `else if (anim >= 0 && !(x < 10.0f)) { A } else if (c) { B }` written with the condition negated and the arms
  swapped (equally `else if (anim < 0 || x < 10.0f)` with B first) was exact (365 -> 0, `branch_polarity`).

Failed: `(anim << 16) >= 0` (320, not exact), nested `if`s (365), `x >= 10.0f` (565), `do { } while (0)` around the
arm (675 to 1215).

Each effect is one function; the cse behaviour behind the last step (a sign extension recomputed after the float
branch) is a hypothesis.
