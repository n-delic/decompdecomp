---
title: table insert: narrow result local, counter start in its declaration, masked countdown
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 129-instruction N64 routine that inserts a value into one of two ten-entry tables (an `if`/`else` with one loop per
arm), shifts the entries below it down with a `u16` countdown that calls `sprintf` for each name, and returns the
position as `s8`. Flags: `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

The source change, and the measured result before and after (scores, the judge used):

The app's judge. A clean rewrite with typed arrays, a `for` per arm and `s32 pos` scored 360.

- The counter's start in its declaration (`s32 pos = -1; u32 i = 0;`, then `for (; i < 10; i++)`): 360 -> 140. The
  order matters: `i` declared before `pos` scored 415 with the narrow `pos`, and 0 after it.
- `s8 pos` (the return type) instead of `s32`: 140 -> 0 (and 360 -> 220 alone). With a word `pos`, the stores after
  `pos = i;` read the copy's register instead of the counter's. That cse prefers the copy is a hypothesis.

From the saved m2c draft (7864): `M2C_UNALIGNED32` four-byte copies as `__builtin_memcpy` (5714 alone), the masked
countdown as a `u16` `for` (6094 alone, 3939 with the copies), then the shift-pair return as a cast and the recomputed
`i * 4` offsets as a `for` over `i` (1929).

Library families: `for_init_to_declaration`, `result_local_narrow_return`, `unaligned_word_memcpy`,
`masked_countdown_for`, `recomputed_offset_for_loop` (decomp-repair), with the older `shift_pair_return_cast`.

Failed experiments that would otherwise be repeated:

- `u8` and `u16` result locals (150, 200), `u32` (140: no change from `s32`).
- Storing the global before the copy (`D = i; pos = i;`, 410) or chaining the two (140 or 410).
- The loop index set to 11 before the table stores (550, 1365).

What is controlled evidence and what is one function:

All of it is one function. Not covered: retyping the draft's byte-offset accesses as arrays, which the rest of the
draft's distance needs.
