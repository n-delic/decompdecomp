---
title: two scratch locals taking turns across a run of field updates
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:
A 28-instruction particle update of an N64 game: six field updates by a literal (`+= 1`, `+= 0x7A`, `+= 0x4A2E`, three
bytes `-= 6`), then a call when one of the bytes is below 8. The target loads each field one statement ahead, uses only
`$v0`/`$v1` in turn, reads the compared byte back from memory and ends `sltiu $v1, $v1, 8; beqz $v1; sb $v0` (the last
store in the delay slot). The app's saved best was the m2c draft at 990 after 26 attempts.

The source change, and the measured result before and after (scores, the judge used):
All scores from the app's judge.
- One expression per field (m2c, a typed struct, `+=`): 990.
- `x = F; x += K; F = x;` with two locals `x` (s32) and `y` taking turns statement by statement: 105. Same
  instructions, `$v0`/`$v1` exchanged.
- End written `x = p->b; y = p->r; p->b = x - 6; if (y < 8U)` with `y` u32: 170 (registers right, the compare's result
  in `$v0` and the store before it).
- `... p->b = x - 6; do { y = y < 8; } while (0); if (y)`: 0.
The same two steps on the draft's `M2C_FIELD` spelling also give 420 (strict alternation), 105 and 0:
decomp-repair `alternating_rmw_locals`.

Failed experiments that would otherwise be repeated:
KMC GCC 2.7.2 (2580), SN 2.7.2 (1205), `-O1` or `-fno-schedule-insns` (1300), a `volatile` read at the compare (1045),
one local for all updates (960), `do { } while (0)` around the updates (235 to 810), an inline helper for the bytes
(1860), `y = y < 8` without the loop (the compare goes to a new register again).

What is controlled evidence and what is one function:
One function. The explanation (a register set once is boosted by the first scheduler; the loop notes stop the flag's
compare being replaced by a new register) is a hypothesis: this compiler has no public source.
