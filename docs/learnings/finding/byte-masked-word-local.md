---
title: Byte-masked word local
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler copied a channel number into an `s32` local
with `channel = byte & 0xFF`, then multiplied it by 16 and compared it for
equality. Changing the local to `u8` preserves the possible values and these
uses. Under gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, the isolated judge
changed from 3175 to 2825. The `byte_mask_local` repair generates the exact
candidate and restricts uses to equality and bounded constant multiplication.
The app judge request was still running when this evidence was recorded.

Reversing the multiplication operands alone scored 3175. Splitting a shared
index temporary scored 4291; combining that split with byte narrowing scored
3936. These are observations on one function, not controlled exact-match
tests. The register-allocation explanation remains a hypothesis.
