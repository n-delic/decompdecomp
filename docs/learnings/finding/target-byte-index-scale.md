---
title: Target-evidenced byte index scale
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler indexed a table reached through a pointer to a
halfword count. Its draft added an index already multiplied by four to that
halfword pointer, making C scale the index again. The target checks the index
against the count and uses a single shift by two before loading a word at the
same base offset.

Casting the base to `u8 *` before the addition corrected the draft's address
calculation. Under gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, the isolated
judge score changed from 3925 to 3775. The `target_byte_index_scale` repair
produces exactly that candidate from the 3925 source when it sees the target
bounds check, shift, base and access offset. This repairs an inaccurate draft;
it deliberately changes the draft's behavior to agree with target evidence.

The score is one function observation, not a controlled exact-match test.
The app judge request was still running when this entry was recorded; 3775
was measured locally and was not yet confirmed by the app.
