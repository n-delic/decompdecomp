---
title: Offset rebase loops are an inline helper with its own base local
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-27
---

A 155-instruction N64 sound-bank start. It turns the offsets in a loaded bank into pointers (one 7-word header run and
three tables of `count` words), then sets up its voices. The target has `move $a2,$s2` in the delay slot of the "already
loaded" branch and `move $a1,$a2` before each of the later loops. The function's profile was SN 2.81, where the clean
rewrite stayed above 3000; the project's KMC profile was the exact one (applied per function).

The source change: the loops are calls of one `static inline` helper that copies its base parameter into a local
(`s32 offset = base;`), and the caller passes a local `base = (s32)bank;` set before the `if`. The app's judge records
0. Measured spellings under KMC: hand-written pointer loops about 2000; the helper adding its parameter directly 1665;
with the `offset` local 0; passing `(s32)bank` straight to each call 115.

From the saved m2c draft (1423 when first recorded under KMC) the library reaches it: `rebase_loop_helper` 188,
`byte_offset_on_pointer` 38 (the draft added `i * 4` to a `void **`, so the compiler scaled it again: `sll 4` for
`sll 2`), `postinc_copy_merge` with `callee_arity_trim` 18 (each alone is worse than 38), `inferred_frame_reservation`
0. The last step is a frame reservation, so the permuter's route carries a pad that the hand-written source does not
need.

Failed: a pointer-typed base added as `base + list[i]` (1815), SN 2.7.2 (3347), KMC `-O1` (6020).

One function; the inline-copy rule is observed from its spellings, not traced through `integrate.c` and cse.
