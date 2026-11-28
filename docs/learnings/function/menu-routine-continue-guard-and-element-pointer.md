---
title: A menu routine, guarded loop body and pointers to constant-index records
kind: function
compiler: SN GCC 2.8.1 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 437-instruction N64 menu routine: a display-list header, then one of two loops over an array of 0x48-byte records
(a name and a position), with an if/else ladder choosing the text call. The saved best was an m2c draft with raw
display-list pointer writes (22062 after 67 attempts).

The source change, and the measured result before and after (the app's judge):

1. A rewrite typed from the assembly (record array, three-float vectors, a three-byte colour copied from a global,
   display-list command macros, `if`/`else` chains, float literals): 22062 -> 4040 on its first compile.
2. A skip written `if (c) { i++; continue; } rest; i++;` as `if (!c) { rest; } i++;`: 4040 -> 3020 alone.
3. `items[2].name, items[2].x, items[2].y` in a call (and the same for `items[3]`) through a block-local
   `Item *element = &items[2];`: 4040 -> 870 alone. The target keeps `items + 2` and `items + 3` in saved registers.
4. Both: 0. decomp-repair `continue_step_guard` then `constant_element_pointer` reach 0 from the draft without them.

Failed experiments that would otherwise be repeated: only one record pointed at (items[2] or items[3] alone) stays at
1835 / 1810 with the guard; both are needed.

What is controlled evidence and what is one function: the two edits are measured separately on one function. That
loop.c is what hoists the two record addresses is a hypothesis. Nothing offers step 1 from the m2c draft.

A float constant used by two calls in a row loads from memory into a saved float register and moves to the argument
register: write the literal `1.0f` to an `f32` parameter, not an `extern f32`.
