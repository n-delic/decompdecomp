---
title: Target indexed table reread after calls
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler's draft cached an indexed table address before
calling callbacks and used that old address afterwards. The target reloads
the table base, shifts the index by four and reads a byte and a float field
from the resulting address after the calls.

Recomputing the address after the callbacks changed the isolated judge from
3775 to 3280 under gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`; the app
recorded 3280. `target_indexed_base_reread` produces exactly the judged source
from the previous candidate. This is a target-evidenced draft correction,
not an equivalent transformation if a callback can change the table pointer.

The score is an observation on one function, not a controlled exact-match
test. Focused tests check the target call, base, index and both field offsets.
