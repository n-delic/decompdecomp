---
title: operand order and frame families three
kind: finding
compiler: SN GCC 2.7.2 -G0 -mips3 -O1 / KMC GCC 2.7.2 -O1
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
Five N64 racing-game functions that hand matches closed (the first four on KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32`, the
last two on SN GCC 2.7.2 `-G0 -mips3 -mhard-float`), checked against the decomp-repair library to see which of the hand
steps the Deep Permuter's families already reach and which they do not.

The source change, and the measured result before and after (scores, the judge used; `prove_chain.py --families`
from the pre-match source, one judge process):
- Element address `list + n` with the add's operands the wrong way (5): `pointer_index_int_form` already gives
  `(s32 *)(n * 4 + (s32) list)` (0). Nothing added.
- `p[i + 0x79]` over a byte pointer (10): `address_association` already gives `*(u8 *)(p + i + 0x79)` (0). Nothing added.
- `base + (i * 0x510 + 0xA8)` regrouped at a call argument (120): `reassociate` already gives it (0). Nothing added.
- A byte pointer plus a scaled byte index under a cast, `*(s32 *)(base + idx)` (5, one `addu` operand order, every
  hand spelling canonicalised by cse): `*(s32 *)(idx + (s32) base)` is 0, `((s32) base + idx)` stays 5. New family
  `byte_pointer_sum_int_first`.
- A float store at a per-index slot written by m2c as `*(&D + (n * 4))` for a 4-byte `D` (150, one `sll 4` for the
  target's `sll 2`): `*(&D + n)` and `*(f32 *)((s8 *)&D + (n * 4))` are both 0. New family `symbol_byte_index_same_size`
  (`narrow_table_byte_offset` takes only symbols narrower than the step).
- Four result buffers one callee fills in turn, m2c's `s32 sp10[4]; char sp20[0x10]; u8 sp30[0x10]; u8 sp40[0x10];
  s32 sp18;` against the game's `s32 buf[4][4]` with `buf[i][2]` reads: the frame differs by the layout of five objects.
  New family `stack_buffers_one_matrix` (`stack_locals_one_array` takes word locals only). The chain from the pre-match
  source with a clean loop: 255 -> 105 (store) -> 0 (matrix with its `rows[i][2]` read).

Failed experiments that would otherwise be repeated:
- From m2c's goto loop (`var_v0 = var_a0 * 0x10` walking `sp10 + var_v0` on an `s32 *`) the matrix gave 240 -> 130 only; the
  loop's byte stride and temporaries are other families' work, the matrix alone does not reach 0 there.
- `((s32) base + idx)`, a parenthesised sum, a temporary for `base + off` and the index re-read from the table all scored 5.

What is controlled evidence and what is one function: each family's 0 was measured with the other parts of the function
already written the game's way; the families are hypotheses about operand order that the judge decides.
