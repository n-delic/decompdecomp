---
title: A two-arm matrix load whose merged copy m2c reads through pointer variables
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-27
---

A 75-instruction N64 function: each arm of an `if`/`else` writes one display-list command and picks one of two 64-byte
matrices, then both reach one 16-word copy to a third. In the object each arm loads word 0 through `%lo(symbol)`, sets
the source register, and the stores and the other loads are shared (jump2 merged them).

The source change from the m2c draft, with the app's judge:

- the 16 statements written in each arm from the arm's symbol, each word as
  `M2C_FIELD(&dst, s32 *, k) = M2C_FIELD(&src, s32 *, k)` (decomp-repair `arm_symbol_copy_tail`): 3950 -> 1080;
- `cmd = list; list += 8;` as a post-increment of a two-word record pointer, which brings back the `move` that keeps
  the old value (decomp-repair `command_post_increment`): 3530 alone, 0 with the copy.

Failed experiments:

- Loads kept as members of the draft's record, `((Rec *) &src)->m4`: 1600 (640 with the post-increment).
- `((s32 *) &dst)[k] = ((s32 *) &src)[k]` on both sides: 640 with the post-increment.
- A source pointer set in the arms and one struct assignment after the join: 7235 (a block-move loop).
- `dst.m[k] = src.m[k]` with one typed record on both sides, in each arm: also exact.

The mechanism (structure members and scalar words ordered differently by alias analysis) is a hypothesis; the scores
are controlled results from one function.
