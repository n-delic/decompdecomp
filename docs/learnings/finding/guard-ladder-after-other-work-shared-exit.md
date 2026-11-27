---
title: A shared fail exit placed between the tests and the work, after other work in the guarded block
kind: finding
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

An 82-instruction N64 update routine (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`):
a null guard around an early-return guard and a computation, then nested guards that fail to two labels after the
outer `if`. The target places that shared exit (`ori; j end; sh`) between the tests and the work, and the outer null
test branches into it.

From the raw m2c draft, with the app's judge (2735):

1. The held temporaries written as the field reread at every use (`field_copy_reread`): 1550. The target reloads the
   field for each store group and recomputes the shift.
2. The `0xFFFF` store typed `u16` instead of `s16` (`field_width`, from the target's `lhu`; `s16` gives `li -1`): 410.
3. The outer test written as `goto` to the labels, the work kept, the labels and tail moved into the ladder's joined
   exit (decomp-repair `guard_ladder_prefix_exit`, PR 2176): 0.

Failed: a temporary holding the field, in any spelling tried (a shared local, inline helpers, `do { } while (0)`
groups, a `volatile` read), stayed at 340 or more or left `move`/`sra` rows; `guard_ladder_early_exit` declined
because the ladder did not open the outer `if`.

One function; the family's behaviour is covered by controlled tests in decomp-repair.
