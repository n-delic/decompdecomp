---
title: byte array global stack pop is a record with an array member
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: verified
date: 2026-11-28
---

An 18-instruction stack pop of a small state record. The saved draft declared the global `extern u8 D_x[];` and read it
with `M2C_FIELD`, under the project's KMC GCC 2.7.2 profile. Profile:
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32` (`-mfp64` was not
tried).

The source: one struct (`top`, a free count, a `u8` depth, an `s32` array of marks), declared
`extern State D_x;`, with `D_x.depth--; mark = D_x.marks[D_x.depth];` inside `if (D_x.depth != 0)`. Written plainly it
was exact under SN and 1415 under KMC (the saved draft: 1365 under KMC, 2030 under SN). Reaching the target's base
register (`lui`/`addiu` of the symbol, offset 0 through `%lo`) needed the global declared as the record; casting the
byte array did not.

From the draft, with the app's judge under the SN profile (decomprepair after pull request 2284):
`m2c_field_record_view` 2030 -> 310, then `guard_temp_sink` 310 -> 0. Before the change the record view only cast the
base (715), and `guard_temp_sink` skipped the temporary because it stood in a `do { } while (0)`.

What is controlled evidence and what is one function: one function. The permuter reaches this only once the function is
on the SN profile; the profile was found by a compiler trial, not by a repair.
