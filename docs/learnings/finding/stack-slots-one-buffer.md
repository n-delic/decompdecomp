---
title: stack slots one buffer
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: an N64 function (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O1 -g`, 0x220 byte frame) that passes the address of one stack slot to a call and the
address of a second slot, 0x44 bytes further up, to another. The machine draft had `M2C_UNK sp18; char frame_pad[504];
M2C_UNK sp5C;` (the pad a frame-size search added between them).

The source change, and the measured result before and after (scores, the judge used): gcc put the pad at the
bottom of the locals, so the slots were at 0x210 and 0x214 (score 2, stack rows only). Separate array declarations put
the second slot at 0x60 (score 1). Exact (0): one buffer, `u8 buf[512];`, with the calls receiving `(M2C_UNK *) buf` and
`(M2C_UNK *) (buf + 0x44)`. The size is the declarations added up (4 + 504 + 4); the distance is the `spNN` offsets'
difference. Two slots at a fixed distance that a pad stands in for are one buffer with a constant offset. decomp-repair
0.6.473 `stack_slots_one_buffer` does it: prove_chain 2 -> 0 from the saved C, first step.

Failed experiments that would otherwise be repeated: all other families on the saved C found nothing (66 children, none
better than 2); `stack_locals_one_array` needs word arrays and gaps of at most 0x20 bytes.

What is controlled evidence and what is one function: one function. The family needs a pad declared in the draft (the
buffer's size is read from it); a draft with no pad would need the target's frame size.
