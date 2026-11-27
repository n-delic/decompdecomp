---
title: halfword store through unknown global
kind: function
compiler: gcc-2.7.2-kmc -O1
evidence: verified
date: 2026-11-27
---

A 145-instruction N64 vertex-coordinate updater (two sine results written into halfwords of 16-byte records, one
six-iteration loop). Compiler `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (project default `-O2`; its per-function
profile was changed through an exact trial).

m2c's draft stores `*(&D + i) = (s16) (s32) x` where it does not know `D`. `unknown_byte_array` declared `D` as
`u8 D[]` and wrote `D[i] = ...`, a byte store (`sb`) where the target has `sh`. The chain from the draft stopped at
620 (4465 start). With the width kept (`*(s16 *) (D + i)`, decomp-repair) `prove_chain` from the same draft reaches 0:
`unknown_byte_array`, `conversion_local_inline`, `assignment_at_use` twice, `record_slot_table` twice (standalone
judge; not a Deep Permuter run).

The hand match was shorter: records `{ s16 v; s16 pad[7]; }` for each base symbol, one `angle` double updated with
`angle = angle / b;` before the call (an inline `__sinf((f32) (angle / b))` put the quotient in the divisor's register
and scored 10). A saved compiler trial at 965 was already close.

One function; the store-width rule is a reading of m2c's output, not a compiler behaviour.
