---
title: Narrow a bounded byte phase and order a calculation
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler used one `s32` scratch for a call result read
only through `& 255`, and later for a channel offset. The channel is masked
to 15 before multiplication by 16, so the offset is at most 240. Narrowing
that scratch to `u8` preserved both uses. Moving an independent pitch
calculation before the channel setup completed the source change.

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, the combined change moved
the isolated score from 295 to 260; the app recorded 260. Narrowing alone
scored 320, and ordering alone scored 295. `narrow_byte_phase_order` proposes
the combined child after checking the masks, range, uses and dependencies.
These are observations on one function, not controlled exact-match tests.
The allocation and scheduling explanation is a hypothesis.
