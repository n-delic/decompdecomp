---
title: fixed point unsigned sqrt
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 42-instruction 20.12 fixed-point square
root of an N64 game: an unsigned word converted to float, scaled, double `sqrt`, scaled back and converted to unsigned.
Full flags `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`; only even float registers appear, so the
project's `-mfp64` profile needs a per-function `-mfp32` one.

The source change, and the measured result before and after (scores, the judge used): from the saved m2c draft (1395
under `-mfp32`, the app's standalone judge), `sqrt_nan_fallback_double` 675, `double_unsigned_conversion` 10,
`float_literal_global` 0 (decomprepair 0.6.362). By hand, `return (u32) (sqrt((f32) arg0 * K1) * K2);` with `u32` types
was exact too; under `-mfp64` it scored 85 with only register rows.

Failed experiments that would otherwise be repeated: before `sqrt_nan_fallback_double` existed the library chain
stopped at 865 (`double_unsigned_conversion` alone). Writing the draft's u32-to-float bias back as `(u32)` changed no
bytes: both spellings scored 10.

What is controlled evidence and what is one function: one function. The chain was run with `prove_chain.py`; the
Deep Permuter was not run.
