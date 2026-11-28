---
title: A shift pair of a just-stored halfword is the halfword read back
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:
A 35-instruction N64 view setup, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. It stores two `s16`
arguments doubled into halfword fields of a global, then stores a quarter of those fields into other halfwords. The
extension of each stored value is `sll $a0, 17; sra $a0, 18` on the argument's register, so m2c writes
`temp = (s32) (x << 0x11) >> 0x12; A = (s16) temp; B = (s16) temp;` and never the read of the field.

The source change, and the measured result before and after (scores, the judge used):
The app's isolated judge. Saved best 910. `A = F >> 2; B = F >> 2;` plus `F = x * 2;` written without its local: 0.
decomp-repair `stored_narrow_readback` emits that as its first child from the saved best.

Failed experiments that would otherwise be repeated:
The read-back with the product locals kept: 60. The product locals written into the stores alone: 850. The read-back
kept behind the `s32` local that held the shift pair: 910. In a hand-written source, chaining the doubled stores
(`F0 = F4 = x * 2;`) scored 530 with chained quarter stores and 450 with separate ones. With separate doubled stores,
`A = B = F >> 2;` (B stored first) scored 80 and `B = A = F >> 2;` 0.

What is controlled evidence and what is one function:
The scores are controlled single edits on one function. That `(x << (16 + k)) >> (16 + n)` equals `(s16) (x << k) >> n`
is arithmetic; why GCC forwards the stored register to the read is a hypothesis (cse), not read in its source.
