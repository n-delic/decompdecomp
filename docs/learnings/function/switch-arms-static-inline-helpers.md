---
title: A message dispatcher whose switch arms return: one static inline helper per arm
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

A 150-instruction message dispatcher of an N64 operating-system library: a `switch` on a message type with four arms
that each call the system and return. `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` (the function had been given `-O1`,
under which the saved goto-ladder best scored 140 and could not close).

Source change and result (the app's judge): the plain `switch` with the arms in place scored 3986 under -O2 (the
compiler kept queue addresses in saved registers, one register more than the target). One `static inline` function per
arm, called as `return helper(msg);`, with the second arm's data pointer read before its first call and a queue's
address held in a local in the two arms that pass it twice: 0. decomp-repair `switch_arm_return_helpers` writes the
helpers.

Failed experiments: the goto ladder under any flags (best 60 with `-O1 -fschedule-insns -fschedule-insns2`); inline
helpers under `-O1` (200); helpers without the address local (120). A compiler detection on the saved C suggested
the wrong flags, since the C was shaped for them.

Controlled and one-function evidence: one function. That inlining is what stops the address sharing is a hypothesis,
not confirmed in the compiler source.

Library reach from the other starts under `-O2` (`prove_chain`): the saved goto ladder 3791 -> 3751, the machine
draft 7926 -> 5581 (`switch_arm_return_helpers` was one of its steps). The draft's arms leave through shared `goto`
tails, which the family does not outline; writing each tail back into its arm first is the remaining gap.
