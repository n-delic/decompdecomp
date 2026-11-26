---
title: staged or local width buffer scale
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1/-O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: five N64 functions of one KMC GCC 2.7.2
game (`-G0 -mips3 -mgp32 -mfp32 -O1/-O2`), each one or two rows off an m2c draft.

The source change, and the measured result before and after (scores, the judge used), each reproduced from the saved
best by a decomp-repair family (fast_judge):
- A byte-replication `t = (a<<24)|(a<<16); w = a | (t | (a<<8));` as `t = ...|(a<<8); w = t | a;` (5 -> 0): `or_stage_absorb`.
- `typedef struct { u16 f0; u8 pad[622]; } S;` read with `lbu` by the target, as `{ u8 f0; u8 pad[623]; }` (200 -> 0):
  `record_first_field_width`.
- `(unsigned char) arg0 == 1` where the target loads `lh 0x36($sp)` from the local buffer `sp10[0x28]` the callee fills, as
  `*(s16 *)(sp10 + 0x26) == 1` (200 -> 0): `buffer_read_for_param_compare`.
- `f(arg1, arg0 + 4)` with `s32 *arg0` (target `addiu 4`, draft `0x10`) as `(s8 *) arg0 + 4`, and
  `*((M2C_FIELD(p, s16 *, 0x14) * 4) + &D)` on a 4-byte symbol as `(&D)[M2C_FIELD(p, s16 *, 0x14)]` (150 -> 0 twice):
  `pointer_byte_step`, extended to pointer parameters and index expressions.

Failed experiments that would otherwise be repeated: `prove_chain.py --families all --children 300-400` from each
saved best found no improving child before these families existed (the existing `reassociate`, `or_chain_order`,
`field_width`, `pointer_byte_step` and buffer families did not fire).

What is controlled evidence and what is one function: one function per repair, proved by the judge; the families'
tests use synthetic C.
