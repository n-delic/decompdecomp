---
title: exiting guard run is a switch
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-26
families: guard_exit_switch, store_run_order
---

What the function (or case) looked like and the exact compiler and flags:

A 376-instruction enemy state machine (SN64 GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`, the
per-function profile applied from an exact `compiler_experiments` trial). Two arms of its outer `switch (o->state)` each
start with one call's result tested against 1 and 2 (`r = check(o); if (r == 1) {...; break;} if (r == 2) {...; break;}`)
and then switch on `o->substate`. In the target, the 1/2 tests compare against `$v0` temporaries made fresh in each test.
The frame is 0x28 with `$s0`-`$s4` saved. Written as the `if` guards, the constants 1 and 2 sat in `$s3`/`$s5` across the
calls and were reused for a later `o->substate = 1;` store, which gave two extra saved registers and a 0x30 frame.

The source change, and the measured result before and after (scores, the judge used):

Written as `switch (check(o)) { case 1: ... break; case 2: ... break; default: switch (o->substate) {...} break; } break;`
in both arms (exact judge, `fast_judge.py`), with `r` kept as a local or with the call inlined, the scores were:
- both arms: 2038 -> 40
- the first arm only: 700
- the second arm only: 1518

The remaining 40 was three stores in another order (`o->f34`, `o->f38`, `o->f3C` written; the target has 34, 3C, 38), which
`store_run_order` finds (40 -> 0). The hypothesis is that GCC expands a `switch`'s compare chain at its end
(`expand_end_case`), so cse, following jumps at -O2, no longer sees the tests' constants beside the later store of the
same constant. This was read from the output, not confirmed in cse.c. decomp-repair `guard_exit_switch` (0.6.98) offers
every run at once as its first child. From the hand draft, `prove_chain` now reaches 0 with it and `store_run_order`.

Failed experiments that would otherwise be repeated:

None by hand: the `switch` was the second candidate. Before the family, the app's `draft_repairs.variants` offered 557
children of the `if` form and none wrote either run as a `switch`.

What is controlled evidence and what is one function:

One function. The family's unit tests are synthetic shapes. The sibling function before it (227 instructions, the same
file) matched from a first hand draft once it had the `-mfp32` profile: that is the profile, not this finding.
