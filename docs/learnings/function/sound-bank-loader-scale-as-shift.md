---
title: sound bank loader: a scale back written as a shift, two table copies
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 sound-bank loader of 147 instructions (the compiler above): two local tables of four ROM address and size pairs
copied from read-only data, a loop that DMAs each pair to a heap rounded up to 16, then the banks handed to the audio
library. The m2c draft (sixteen word locals, byte cursors) stood at 6441.

The source change, and the measured result before and after (scores, the judge used):

Isolated judge (`fast_judge.py`), confirmed by the app for the exact one.

- A clean source (struct copies `Table banks = D_X;`, a record array indexed by the counter): 40, eight register rows.
  The target loads an entry's size into `$v0`, adds, shifts down into `$v0` and shifts up into `$s0`.
- `size = (e + 0xF) / 16 * 16;`, `16 * ((e + 0xF) / 16)`, an inline helper returning the same, and m2c's
  `((u32) (e + 0xF) >> 4) * 0x10` all compute in `$s0` (40).
- `size = ((e + 0xF) >> 4) << 4;` is exact (40 -> 0). decomprepair `shift_back_scale`.
- On the m2c draft, the two tables as arrays filled by `__builtin_memcpy`: 6441 -> 2231 (`symbol_stack_block_copy`, which
  until now took one buffer only); `pointer_byte_step_all` then gave 1331.

Failed experiments that would otherwise be repeated:

Block-local size variables (one per DMA) still computed in the saved register. A signed `s32 size` with a `(u32)` cast
on the sum scored 745.

What is controlled evidence and what is one function:

One function. The destination is a local; a store to a record member written `/ 16 * 16` already matched. Why GCC 2.8.1
expands the shift differently from the multiply here was not read in its source: a hypothesis.
