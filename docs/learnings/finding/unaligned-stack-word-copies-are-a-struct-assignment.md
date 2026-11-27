---
title: unaligned stack word copies are a struct assignment
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: exact-match
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 save-file check (74 instructions, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`) starts a
local name from a constant: `lwl`/`lwr` from the constant, `swl`/`swr` to the stack, 16 bytes. m2c writes four 4-byte
locals, each `spX = M2C_UNALIGNED32(M2C_FIELD(&sym, M2C_UNK *, K));`.

The source change, and the measured result before and after (scores, the judge used):

The app's judge. A `typedef struct { char c[16]; } GameName;` local assigned from the constant is the block move.
- raw m2c draft: 4885; the four copies as one byte-record assignment (decomp-repair `unaligned_stack_struct_copy`): 839.
- `nested_guard`: `if (err != 5 && err == 0)` folds to `err == 0`; nesting the two tests keeps both branches: 22.
- `stack_gap_padding` (the unused 0x100 buffer a matched sibling also declares): 12; `stack_object_extent` (the 4-byte
  output as an array, an 8-aligned slot): 0.
A hand rewrite modelled on the matched sibling in the same save code matched on the second judged spelling.

Failed experiments that would otherwise be repeated:

`!(err == 5 || err != 0)` folds as the `&&` spelling does (815 in the typed rewrite).

What is controlled evidence and what is one function:

All of it is one function. That GCC copies a byte-aligned record with `lwl`/`lwr` is general; the block move's order
for other sizes is not measured here.
