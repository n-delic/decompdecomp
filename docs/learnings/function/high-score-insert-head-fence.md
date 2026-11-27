---
title: Contiguous prologue saves need an empty do-while at the head (high-score insertion)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

A 115-instruction high-score insertion routine in an N64 game (two players, a ten-entry table, a name shifted with
each score), `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Saved best 3975 (m2c gotos)
after 86 attempts; 0 on the app's judge.

A plain rewrite (u16 counters, `for` loops, array indexing, `s8` pair on the stack) judged 1100. Then:

- The target saves `$ra`, `$fp`, `$s7`..`$s0` in one run before the first body instruction; ours had body
  instructions among the saves. An empty `do { } while (0);` as the first statement: 840 -> 0 on the final source
  (controlled: that line alone). The same fence around the first two stores stopped at 180. Why it works (a scheduling
  boundary; maybe an empty debug macro in the original) is a hypothesis.
- `if (i < 9) { for (j = 9; j > i; j--) ... }` stood at 60 (two instructions swapped in the preheader); the `for`
  alone, or `j = 9; if (i < 9) do ... while (j > i);`, was exact. `if (i < 9) { j = 9; do ... }` stayed at 60.
- `pos[1]` of an `s8 pos[2]` read for a truncated shift needs `(u8) pos[1]` (`lbu`): 200 -> 0. Declaring the array
  `u8` loses the `li -1` stores.

Families (decomp-repair): `prologue_save_fence`, `implied_loop_guard`, `local_element_read_sign`; greedily they take
the 1100 rewrite to 0 (1100 -> 260 -> 60 -> 0).

Failed: other profiles were no nearer (KMC 2.7.2 -O2 3755, -O1 3625; SN 2.7.2 builds 2612 to 3915), and `p = 0`
before the stores or `pos[0] = pos[1] = -1` left 900.

Not automated: the step from the m2c goto loops to the plain `for` loops was by hand.
