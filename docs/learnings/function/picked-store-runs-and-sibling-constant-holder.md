---
title: picked store runs and sibling constant holder
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 object spawner, 134 instructions, SN GCC 2.8.1 (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`). It formats a score, places the object by player number twice, then allocates three linked
child objects and stores a run of fields into each. The m2c draft was 2740 in the app's judge.

The source change, and the measured result before and after (scores, the judge used):

Edits from the draft, all needed (app judge, exact at the end):

1. Load widths: the draft read a word table and a halfword table through `char` symbols (`asm_byte_table_deref_width`;
   2740 -> 2395).
2. `v = A; if (c) { v = B; } p->x = v; p->y = K; p->z = K2;` written as `if (c) { x = B; y; z } else { x = A; y; z }`
   (`conditional_store_run_arms`; 2395 -> 700). With only the first one or two stores in the arms the rest of the
   function still scored 1350 where all three was exact: jump2 merges the identical tails.
3. The single picked store of each child block the same way (`conditional_store_arms`, which declined m2c's
   `M2C_FIELD(...)` spelling until now).
4. A parent byte copied to a child moved up beside the store it follows in the target (`statement_slide_far`).
5. The non-zero literals of the two sibling child blocks held in one local, `held = 2; a->state = held; ...
   held = 0x408; a->flags = held;` in both (`sibling_constant_holder`; 60 -> 0).

Failed experiments that would otherwise be repeated:

All 24 orders of the four statements of each child block stayed at 60 or worse. The shared local for the flags literal
only was 470; one pointer variable shared by the three allocations instead of three was 700 (same neighbourhood) and
110 with the holder in place.

What is controlled evidence and what is one function:

One function. The explanation of the holder (the literals are block-local quantities local-alloc.c ranks before the
longer-lived byte; a local set in two blocks is left to global.c) matches the `-dl -dg` priorities of the compile
without it; why global.c then ranks the local above the block's pointer was not traced.
