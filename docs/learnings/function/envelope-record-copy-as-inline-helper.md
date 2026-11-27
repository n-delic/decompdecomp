---
title: envelope record copy as inline helper
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 347-instruction note routine of an N64 sound library. It was saved under the project's SN GCC profile at 14165; the
exact profile is KMC `-O2 -G0 -mips3 -mgp32 -mfp32`, as its matched neighbours use. Plain library-shaped C scored 5995.

The source changes, with the exact judge's scores:

- a goto loop over the command bytes (no hoisted jump table), `s32` note, one local per envelope field: 3885, then 2210
  with a struct typo fixed;
- the fixed-length test as nested `if`s with the byte reading written in both arms, a block-local velocity byte: 630;
- a separate `u8` for the envelope speeds, a `u8` product before the signed-byte conversion: 415;
- the envelope copy as a `static inline` function taking the record pointer, which copies its parameter into a local
  before reading: 20 (decomp-repair `pointer_run_helper`). Written in place the first read goes through the pointer's
  own register; the target reads through `$v0` and copies it to `$a1` in the delay slot;
- the signed-byte conversion held in the `u8` local the command loop already uses (`cfg_integer_lifetimes`), and the
  single-use drum pointer inlined (`reload_temp_inline`): 0. `prove_chain.py` finds these three from the 415 state.

Failed experiments: the helper using its parameter directly (325), a copy in a block of the function itself (35), a
pointer assigned through a second local (35), `if/else` and conditional-expression spellings of the zero clamp
(290 to 370 without the helper), an `s32` speed (1970 and up), inline helpers for the signed-byte conversion (840 to
925).

This is one function. Why the helper's parameter copy survives (a pseudo of its own from integrate.c) is a hypothesis,
not read in the compiler source.
