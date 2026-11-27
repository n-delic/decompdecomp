---
title: stack records cleared by a fill, members written as separate locals
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An 89-instruction N64 asset loader with three 0x98-byte tables on the stack (19 address/size pairs each), each cleared
with `memset` and given one entry. m2c wrote `u8 sp10[0x8C]; s32 *sp98; s32 sp9C;` for each: the member stores were
dead and dropped, and the frame was 0x18 bytes short. Symptoms: missing `sw` of the constants after each `jal memset`,
a smaller frame, `lbu` where the target has `lw`.

The source change, and the measured result before and after (scores, the judge used):

The app's judge, from the saved m2c draft, one step at a time (decomp-repair families in brackets):

1. The filled array grown to the fill's length, the locals inside it made members of it: 2402 -> 660
   (`fill_extent_locals`).
2. `*(bytes + off)` cast to the word the target loads: 660 -> 460 (`stack_array_access_width`).
3. `ALIGN(base + size)` as `base += size; ALIGN(base)`, `base` never read again: 460 -> 220
   (`dead_operand_accumulate`).
4. The global read into a local moved above the first `memset`: 220 -> 0 (`global_read_above_fill`).

A typed rewrite (`Entry table[19]` with member stores, the global read in the local's initialiser) was exact on the
first compile.

Failed experiments that would otherwise be repeated:

Greedy `prove_chain --families all` found nothing in the first 120 of 2,205 children of the draft, or in the first 70 of
1,950 after step 1; none of those 1,950 children contained edit 2, 3 or 4.

What is controlled evidence and what is one function:

All measured on one function. That a fill of a local array cannot write a global is the C rule that makes step 4 the
same program, not a compiler claim.
