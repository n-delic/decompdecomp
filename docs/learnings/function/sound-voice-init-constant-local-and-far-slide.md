---
title: a saved constant local and a store three places down (sound-voice initialiser, 440 to 0)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 66-instruction N64 sound-voice initialiser: a global guard, a call that returns an id, a run of field stores, an
allocated second voice filled from the first. Compiler `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64` (the function's own profile). The saved best (440, a machine draft with the search's
`int __repair_constant = 2;`) is exact after five steps, scored with the app's judge:

1. write the constant local as the literal: 440 to 230 (the `addiu $s1, $zero, 2` leaves the prologue).
   `constant_local_restore`.
2. copy the second voice's field up, right after the link stores: 230 to 110. `field_copy_group`.
3. move the word store `0x14 = 0x100` below the three byte-constant stores after it: 110 to 80.
   `statement_slide_far` (it was the 48th of 78 slides, past `statement_slide`'s cap of 40).
4. swap the second voice's word copy with its byte constant: 80 to 20. `statement_slide`.
5. swap the last two byte stores before the call: 20 to 0. `store_run_order`.

A typed rewrite from the assembly reached the same bytes: the call's `u16` result in a local stored to both fields,
the field copy moved up, and a grid of the first block's five stores (120 orders).

Failed experiments: moving the child field copy above the sibling stores without the literal made it worse
(440 to 650); swapping the last two byte stores alone scores 450.

One function is the evidence; that the compiler loads a constant local where it is set is a reading of the diff, not of
the compiler source.
