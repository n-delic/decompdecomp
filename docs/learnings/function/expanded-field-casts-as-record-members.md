---
title: expanded field casts as record members
kind: function
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 39-instruction N64 list-node fade
(colour bytes stepped up or down, a node unlinked and a global count decremented), `sn-n64-281 -G0 -mips3 -O2 -mgas
-meb -mcpu=VR4300 -mhard-float -mfp64`. The saved best was an autopilot draft with every field written as
`(*(T *)((char *)(p) + K))`, which is `M2C_FIELD` expanded; the target loads the count's `lui` in a branch delay slot
and ours did not.

The source change, and the measured result before and after (scores, the judge used): the app's standalone judge.
Saved best 675. Casts written as record members (decomp-repair `expanded_field_record_view`) 105; the parameter copy
dropped (`parameter_copy_drop`) 20; `count--;` moved below the member store (`typed_member_slide`) 0. `prove_chain.py
--families` with those three reaches 0 from the saved best.

Failed experiments that would otherwise be repeated: on the raw-cast best, swapping the two last statements alone
(860), removing the one-shot loop alone (1265) and both (1450) were all worse. `m2c_field_record_view` and
`param_record_view` offer nothing on the expanded spelling.

What is controlled evidence and what is one function: one function. That the member form lets the scheduler move the
global's address load above the stores is read from the output, not from the compiler's source (hypothesis).
