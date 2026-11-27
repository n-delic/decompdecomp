---
title: A float literal passed in an integer register keeps its lui out of the jal delay slot; a narrow parameter's masked read wants the word parameter
kind: finding
compiler: sn-n64-272-0001 -G0 -mips3 -O1 -mgas -meb -mhard-float
evidence: observation
date: 2026-11-27
---

What the function looked like: a 64-instruction N64 function (SN GCC 2.7.2, `-G0 -mips3 -O1 -mgas -meb
-mhard-float`) taking a slot index, ending `lw $a0, 0($s0); lui $a1, 0x40A0; jal f; nop`.

The source change, 165 -> 5 -> 0:
1. The callee's prototype `(s32, s32)` with `0x40A00000` as `(s32, f32)` with `5.0f`: o32 passes a float after an
   integer first argument in `$a1`, and the float constant is a `movsf` whose template is longer than one word, so
   reorg never puts its `lui` in the call's delay slot (the integer `lui` went there). 165 -> 5.
   decomp-repair `float_argument_bits` already wrote this but required `-mgp32`/`-mabi=32`; SN's N64 profile names
   no ABI, so it now also accepts `-meb`, `-mips1` and `-mips2` (0.6.295).
2. `void f(s16 arg0)` as `void f(s32 arg0) { s16 idx = arg0; ...}` with the `(u16) arg0 < 0xC` test on the word
   parameter and every index through `idx`. With an `s16` parameter cse rewrites the sign-extended reads to the
   incoming word copy but `(u16) arg0` stays a zero-extension of the `s16` pseudo, so the test read `$s0` where the
   target's `andi` reads `$s1`. 5 -> 0. decomp-repair `narrow_parameter_wide_copy` (0.6.295).

Failed experiments: the masked test through the narrow local (5); the word parameter with `(s16)` casts and no local
(115); a `u16` parameter (5); four spellings of the test and the index (all 5, identical code).

Evidence: one function, app judge. The saved best's `do { } while (0)` around the first index is not needed (0
without it).
