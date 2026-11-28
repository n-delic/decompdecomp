---
title: a halfword constant stored twice shares one register; the reloaded one came from its own int local
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 301-instruction N64 font table initialiser (two loops over 16-byte records, then constant records, then ten asset
loads), SN GCC 2.8.1 with `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Judged with the app's
isolated judge; the best the app records is 120, not a match.

**The family's step.** The last record's first field is 8, a value every earlier record had stored into its second
field. The target loads it again; ours reused the register. `w = 8; table[47].x = w;` with `s32 w` took the score
from 595 to 120 (decomp-repair `coincident_constant_local`). The explanation (a `SImode` pseudo that cse does not
share with the `HImode` constant) is a hypothesis from the outputs.

**The other steps, each measured on this function only.**
- The loops are `while (1) { if (i < N) {..; i++;} else break; }`: a `for` loop is rotated and loses the top test.
- The loop's record pointer is `table = D; row = table + 'A'; g = row + i;`: indexing `D[i + 'A']` folds the base to
  one `lui/addiu` pair, and the target has `la base; addiu base, 0x410`.
- A static inline setter with `s32` parameters gives `move $a1,$a2` for the stepped value's start.
- The stepped right edge computed into its own `s32` before the record address puts the two induction increments in
  the target's order.
- The constant records are in the order the target's grouped stores show, not column order, and the first four
  records' first fields go through one reused `s32` local (the target keeps them in one register, `$t5`).

**Open.** In each loop's preheader the target writes `move $a1,$a2` before the `lui/addiu/addiu` of the record
pointer; every variant that has the loop body exact puts it after. The first scheduler orders the two induction
variables' initial values by the order loop.c emitted them, and the variants that give the preheader's order
hoist the two right-edge stores above the first store in the body.

**Failed.** KMC GCC 2.7.2 (55246 and up), `-O3` (no change), `-fno-schedule-insns` (worse), a stepped pointer biv
(3995), a stepped right-edge biv (885, 1885), a fourth argument on the first load call (worse).
