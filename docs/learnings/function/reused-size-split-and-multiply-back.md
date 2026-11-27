---
title: A reused size split where a value starts afresh, and a round-down written as a product
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 228-instruction N64 asset loader (picks one of
five 16-byte names with `strcpy` of a literal, reads an index, DMAs and decompresses a file). A matched sibling loader
was in the project; its C, adapted, started at 1703 where the m2c draft stood at 9004.

The source change, and the measured result before and after (scores, the judge used), all with the exact judge:

- Buffers declared below the name array gave the target's frame (1703 -> 845), and the start-up statements in the
  target's order 310.
- One `u32 size` held the index's length and later a file's size, swapped and rounded in place. Two locals, with
  `size = SWAP32(size); size = ...(size);` kept in the second: 310 -> 40. One local per assignment: 595.
- `size = ALIGN16(size);` (`((x + 0xF) >> 4) << 4`) computed in `$v0`; `((size + 0xF) >> 4) * 0x10` computed in the
  local's `$s0`: 40 -> 25.
- `f(head, ALIGN16(dest));` -> `dest = ALIGN16(dest); f(head, dest);`: one more reference, and `dest` and the `%hi`
  of a global exchanged `$s7`/`$fp`: 25 -> 0.

decomp-repair: `local_web_split` now also keeps self-reading chains whole, `shift_back_multiply` writes the product,
and `call_argument_inplace_update` already wrote the last step. Their own output chains 310 -> 40 -> 25 -> 0.

Failed experiments that would otherwise be repeated: `size += 0xF; size >>= 4; size <<= 4;` was no better than the
product (25) and three statements without the last shift gave 35; a `do { } while (0)` around the destination's load
made it far worse (870).

What is controlled evidence and what is one function: one function. That the extra reference decides the saved-register
order was read from the allocation table; why the scaled spelling is computed in place is a hypothesis.
