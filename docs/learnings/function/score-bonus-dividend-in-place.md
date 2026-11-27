---
title: a dividend reduced in place and a dead high multiply, in a bonus-chain updater
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: an N64 object updater (124 instructions) that
adds to a score table, spawns two effect records and steps an angle by a remainder of 5. `sn-n64-281 -G0 -mips3 -O2
-mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

The source change, and the measured result before and after (scores, the judge used), all with the app's judge:

- Typed rewrite, `rem = (p->angle - p->target) % 5;`: 50. `rem = p->angle - p->target; rem %= 5;`: 10 (the subtraction
  overwrites the dividend's register, as in the target). The last 10 rows were an 8-byte frame hole: an unused
  `struct { s32 x, y; } vel;` (also `s32 vel[2];`) gave 0. That local is scaffolding as far as the bytes tell.
- From m2c's draft (2150) with its other edits made: inline `(..) % 5` 510; named and split above the preceding
  constant store 0; split below that store 510; `repair_dividend = (..) % 5;` above it 40.
- m2c's unread `temp_hi = MULT_HI(x, 0x66666667);` (also passed to a callee that takes no such argument): 765 with only
  the argument dropped, 0 with the statement removed too.
- decomp-repair families `dividend_in_place` and `dead_multiply_high` (decomp-repair #2135).

Failed experiments that would otherwise be repeated: the same candidate under `gcc-2.7.2-kmc -O2` scored 1800.

What is controlled evidence and what is one function: one function.
