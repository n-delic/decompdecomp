---
title: fixed point length or loop and shifted squares
kind: function
compiler: sn-n64-281 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 39-instruction N64 fixed-point vector length: both arguments made absolute by a call, halved together while either is
above 0x10000, the two squares shifted down by 12 and summed into a square root, the result shifted back.

The source change, and the measured result before and after (app judge):

- m2c's shape (`loop: if (x <= K) { if (y > K) { x >>= 1; goto step; } return ...; } else { x >>= 1; step: ... goto
  loop; }`, each call result in its own local): 670.
- `while (x > K || y > K) { x >>= 1; y >>= 1; n++; }` (`goto_or_loop`): 95.
- each call result written back into its parameter, `x = absS32(x);` (`parameter_call_result`): 20, four register rows
  (the first `mflo` in `$a3` where the target has `$v0`).
- `u32 a = x * x; u32 b = y * y; a >>= 12; b >>= 12; return f(a + b) << n;` (`shifted_operand_locals`): 0. Shifting
  each before computing the next is also 0.

Failed experiments that would otherwise be repeated:

- Locals for the squares with the shifts left in the sum (`(a >> 12) + (b >> 12)`), or shifted in the initialiser
  (`u32 a = (u32)(x * x) >> 12;`): 20.
- Only one square in a local and shifted in place: 25 or 30. One shared `sum` local with `+=`: 25.
- With the locals already there, `last_use_shift_in_place` (`(a >>= 12) + (b >>= 12)`) is also 0.
- The same source with `-O1`: 940.

What is controlled evidence and what is one function: each pair of scores above is one source change on one function.
