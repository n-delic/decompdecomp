---
title: m2c's unassigned unkspNN halfword is the low half of a stack word the C stored whole, and the words are one array
kind: finding
compiler: SN GCC 2.7.2 (sn-n64-272-0001), -G0 -mips3 -O1 -mgas -meb -mhard-float
evidence: verified
date: 2026-11-27
---

What the function looked like: an 80-instruction N64 routine that stores three words (`sp10 = x * 8; sp14 = y * 8; sp18 = z * 8;`) and later
copies the halfwords at `0x12`, `0x16`, `0x1A` of the frame (`lhu`) into a record. m2c declares those three as separate locals
(`unksp12`, `unksp16`, `unksp1A`) that nothing assigns. Earlier hand steps put a nearby value there (`= temp_s4;`), a wrong program that
stood at 333.

The source change, and the measured result before and after (the project judge): on a big-endian MIPS the halfword at +2 of a word is its
low half, so the read is `(u16) sp10[0]`, and the words are declared as one array `s32 sp10[3]` (indexes 0..2, not 1..3). With three
scalar locals instead the same C scored 638 (the compiler keeps scalars in registers, so no `lhu` from the frame); with the array it was exact
(333 -> 15 -> 0; the last 15 was the modulo temporary written `u16`, a separate step). The library family `stack_word_half_read` (decomp-repair
0.6.256, `stack_word_half_view` is its `((u16 *) sp10)[1]` spelling) makes the step from the m2c draft: 2342 -> 0 on the reconstructed draft.
A second function (`unksp2 + D`, `unksp6 + D` inside sums, array already declared, `((u16 *) sp0)[1]` form) went 315 -> 85 by the same family; its
width comes from the target's own `lhu`/`lh` of the slot, which the sum's store cannot tell.

Failed experiments that would otherwise be repeated: scalar word locals (638); `sp10[1..3]` (one slot out, 333). A third function
(a transposed 4x4 to 3x3 copy, 270 -> 0) needed typed 2D arrays from the shared header and u8 counters: the same loops over flat indexes
scored 435, so that step is not a repair (the existing families took the m2c draft 3247 -> 60).

What is controlled evidence and what is one function: two functions, one exact; big-endian layout only.
