---
title: A load at the end of a store run belongs in a local above it, with the wrapper opened
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An 81-instruction N64 object-spawning function (SN64 GCC 2.8.1) stood at 120, four reordered rows. Twice it copies a
word into a new object, stores three bytes of 2 and stores `source->u16_field - K` in the new object's halfword. The
target loads the field right after the word store and subtracts after the byte stores (`lw; sw; lhu; sb; sb; sb;
addiu; sh`). The saved best loaded it after the byte stores.

The source change, in both blocks: the `do { } while (0);` around the word copy removed, `t = field;` written right
after the copy, and `t - K` stored after the byte stores. Judged by the app's judge: 120 -> 60 per block, 0 with both.
No scaffolding is left. decomp-repair `store_run_load_stage` reaches 0 from the saved best in one step.

Failed experiments, all under the same profile:

- The local with the wrapper kept: 120 (the instruction after a loop note depends on everything before it, so the
  load moved but the subtraction came out right after it).
- The wrapper opened without the local: 240.
- The halfword store written first, `const` on the load's pointer, a struct type for both objects, and `u16` or `s32`
  locals with the wrapper kept: 120 or worse.
- Also exact, with scaffolding: the local ahead of a wrapper around the three byte stores (not used).

The explanation through the loop note is a hypothesis from the `-dS -dR` notes, not checked in the compiler's source.
One function with two identical sites; not a controlled study.
