---
title: a dead identical-arm guard on an unassigned flag and a halfword local sharing its pseudo: each alone worse, together 0
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 74-instruction audio function (clamp a halfword field after adding a signed byte, then test whether a slot id and two
bytes of the record select the playing clip and, if so, call a helper) sat at 60 with 12 register rows, all in the
flag test: the target holds the flag in `$v0` and the two compared values in `$v1`/`$v0`, the saved best used `$a3`
for the flag.

The saved best (m2c's draft plus an earlier agent's edits) carried two pieces of scaffolding: an identical-arm
`if (temp_a1) {S} else {S}` on a local read before any assignment, and `else if (temp_v0) {temp_v0 = 0} else
{temp_v0 = 0}` on the flag local, also read unassigned. The flag's pseudo was therefore live from the function's
entry (49 insns in `-dg`'s table, priority 0.673, allocated after the short pseudos had taken `$v0`/`$v1`) and got `$a3`.

| edit from the saved best | score |
| --- | --- |
| saved best | 60 |
| collapse the flag's dead `else if` (`identical_arm_collapse`) | 110 (the register rows all match; the target's `andi $v0,$v0,0xff` before the test is gone, the dead read had kept it) |
| merge the halfword local into the flag local (`merge_integer_lifetimes`) | 690 |
| collapse the top `if (temp_a1)` and merge | 75 |
| collapse the flag's `else if` and merge | 0 |

The merged local is `s32`; the two edits together put the loaded halfword and the flag in one pseudo whose range no
longer starts at the entry. Whether the `andi` returns because the combined pseudo's set of `lh` has unknown high bits
is a hypothesis (combine's nonzero-bits tracking; not read in the source here); the measurement is the evidence.

Failed on the way: writing the whole tail as a boolean `&&`/`?:` expression (2340-2495: jump threading copies the
flag test into each arm, so the shared join's `andi`/`beqz` is lost), `u8`/`u16`/`s8` for the flag (990-1205),
`(u8) flag` or `!= 0` in the test (110: nothing changes, the and is still dropped).

One function; the pair is not controlled. decomprepair `collapsed_guard_lifetime_merge` (0.5.652) offers the pair as one
child for each of the first four collapses; from the saved best the second one is the exact child. Scaffolding in the
matched source: the other identical-arm `if (temp_a1)` on an unassigned local, a `volatile s32 stack_pad[4]` for the
frame's 16 unaccessed bytes and `volatile s16` stores for the read-back of the stored halfword.
