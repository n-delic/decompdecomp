---
title: scalar global vs array element store order
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 588-instruction N64 camera update (`-mfp32` per-function profile). `cam->yaw += f(stickX, 3) * 80;` is followed by
`cam->pitch -= f(stickY, 3) * 80;`. The target keeps the load of the second stick global after the store of
`cam->yaw` (`sw v0, 0x10(s2)` then `lui v0; lw a0, %lo(stickY)(v0)`); ours moved the load above the store.

The source change, and the measured result before and after (scores, the judge used):

GCC's alias test (`MEM_IN_STRUCT_P` heuristic in `true_dependence`): a scalar at a fixed address never conflicts with a
field reached through a varying pointer, so the scheduler may hoist it over the store. Declaring the global
`extern s32 stickY[];` and reading `stickY[0]` makes it an in-struct reference too, so the load stays after the store.
The first stick global stays a plain scalar (its target load is free to move). 695 -> 175 (fast_judge), and exact after
the next change. This is the same mechanism `global_array_cast_form` relies on.

Failed experiments that would otherwise be repeated:

Reordering the statements or using a temporary for the product does not change it: the scheduler decides, not the
source order.

What is controlled evidence and what is one function:

Controlled: the declaration and the `[0]` are the only edit. One function.
