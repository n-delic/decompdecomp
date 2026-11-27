---
title: a byte stored through a cast and divided again was read back from the member
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: an N64 sprite pulse routine of 50
instructions, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. A shifted counter is stored into a byte
member, the counter is stored back, and the byte's remainder by 5 is stored into the same member. The target stores the
byte, then `andi` of the stored register, then `multu` by `0xCCCCCCCD`. The m2c draft wrote
`F77 = (s8) v; F34 = t; r = (v & 0xFF) % 5;` and scored 1495: with an `s32` local the remainder is signed
(`mult` by `0x66666667`).

The source change, and the measured result before and after (scores, the judge used): `r = *(u8 *)member % 5;`, the
member read back, is exact (0, the app's judge) as the only edit. decomprepair `stored_member_readback` now offers it:
the store may be written through a cast of the member's width, and a store to another member of the same base between
does not end the walk. `prove_chain` reaches 0 from the saved draft in one step.

Failed experiments that would otherwise be repeated: `% 5U` alone gives `multu` and scores 635, not exact; adding
`do { } while (0)` around the shift then reaches 15 with three registers left, which the read-back makes unnecessary.
Writing the mask on the shift expression itself (`((t >> 2) & 0xFF)`) stayed at 635.

What is controlled evidence and what is one function: one function. That cse forwards the stored register into the
byte read and leaves the `andi` is a hypothesis from the output, not read from the compiler's source.
