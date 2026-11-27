---
title: A struct-member store in the delay slot after a scalar global load, from an m2c draft
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---


A 158-instruction N64 object update (a counter, a spawn every 20 frames, a field-by-field particle setup), SN GCC
2.8.1 (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). The saved best was the autopilot's m2c
draft at 330 with two differences:

- `object->timer = 0; if (D_scalar == 0)` written through `M2C_FIELD`: the store stayed above the global's load and the
  branch delay slot was a `nop`. The target loads the global first and stores the zero in the delay slot.
- `*(s32 *)((D_index * 4) + D_table)` with `extern char D_table[]`: the table's `lui` came after the index's.

The source change, and the measured result (the app's judge):

- the table as `extern s32 D_table[]` read `D_table[D_index]`: 330 -> 160;
- the reset as a typed struct member store (`((struct { char p[0x34]; s32 timer; } *) object)->timer = 0;`): 160 -> 0.

The library reaches the same without a hand edit: `symbol_address_local` (330 -> 160), then `inferred_record_layout`
(160 -> 0), both through `prove_chain.py` from the saved draft. No new repair was needed. No repair pass had run on the
330 draft when it was matched by hand.

Controlled and one function: each of the two source changes was measured alone against the draft's score. That a
struct member store may pass a scalar global's load because the scheduler treats the two as different objects
(`sched.c` `true_dependence`) is a hypothesis here, not read from this compiler's source; the same behaviour is
recorded for other functions (see the `record_member_plain` entries).

Tool note: the app renamed the function when it matched, so `prove_chain.py` and `fast_judge.py` run after a match
need the new name (`--function`), or they answer that the project has 0 functions of the old one.
