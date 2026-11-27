---
title: returned gfx cursor macro and void callee
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 46-instruction N64 routine that scales a matrix, adds one display-list matrix command through the `Gfx *` it was
given, concatenates matrices and returns the cursor. The m2c draft stored through the `void *` parameter, returned
`arg0 + 8`, passed `arg0` as a fourth argument to the last callee, declared that callee `M2C_UNK` and an address-only
global `s32`.

The source change, and the measured result before and after (scores, the judge used):
Four edits, judged by the app's judge from the draft at 1180, reach 0: the stores as the SDK macro (`_g = gfx++`, then
the two words), the stray cursor argument dropped, the unused-result callee declared `void`, the global declared
`u8 *`. Each alone: global 920, void 940, cursor with the argument kept at the end 1155, argument dropped alone 1205.
Greedy chain with the library families `returned_gfx_cursor`, `address_global_pointer`, `discarded_result_void`:
1180 -> 870 -> 300 -> 0.

Failed experiments that would otherwise be repeated:
Struct-typed members instead of `M2C_FIELD` stores without the other edits did not move the score (870).

What is controlled evidence and what is one function:
All measurements are from this one function. Why the pointer type and the `void` return move the order is a
hypothesis (alias class of the pointer load; `$v0` set by a value call), not checked in the compiler source.
