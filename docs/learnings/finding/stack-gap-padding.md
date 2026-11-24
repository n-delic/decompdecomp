---
title: stack gap padding
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: a 49-instruction N64 function
(KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`) that takes the address of one word local (m2c: `s32 sp60;`, `addiu $a1, $sp, 0x60`).
The target's frame is `addiu $sp, $sp, -0x80` with the word at `0x60($sp)` and the saved registers at `0x68..0x7C`;
the draft's was `-0x30` with the word at `0x10`. Saved best 16, every differing row a stack row (frame size, saved-register
slots, the local's offset).

The source change, and the measured result before and after (scores, the judge used): one unused local declared after
the others, `s32 pad[20];` (0x50 bytes), took 16 to 0. `u8 pad[0x50]` is the same; `s32 pad[16]`, `s32 pad[21]` and `u8 pad[0x40]`
stay at 16 (the frame, not the array's type, is what matters, and it has to be exact). The array has to be declared after the
local whose offset moves: GCC allocates locals downward from the saved registers in declaration order, so the later
declaration sits lower and the address-taken word keeps the offset the target gave it (declared first, it would sit at 0x10
again; hypothesis, not read from cc1). The size comes from the target alone: the lowest `spNN` m2c declared
(0x60) minus the outgoing-argument area (16 bytes for a function that calls). A function with no stack object
at all pads up to its lowest saved register the same way (a 0x98 frame holding only `$ra`/`$s0` at 0x94/0x90: `s32 pad[32];`).
The same paddings recurred before in matched functions of this project (`pad[32]` / `sp10[32]`, 128 bytes, three times,
and this 0x50), so the size is common, not a one-off. decomp-repair `stack_gap_padding` proposes both placements
(after the last local, and first); from the saved best 16 it reaches 0 in one step.

Failed experiments that would otherwise be repeated: the existing families could not reach it. `unused_frame_local`
is for leaves only; `unused_frame_padding` skips a function that names an `spNN` local and stops at 64 bytes;
`inferred_frame_reservation`, `measured_grow_unused_frame_array` and `stack_gap_padding`'s measured siblings take a
delta of 8..32 and want every changed row to agree; `stack_object_size` sizes the `spNN` object itself (an 8-byte `sp60`
leaves the frame at 0x30).

What is controlled evidence and what is one function: one function proves 16 -> 0 (observation); on the project's 1,815 saved
sources the family proposes in 12 and leaves the matched ones alone (they already carry the array); both recurring
paddings (0x50 with an address-taken local, 0x80 with none) are reproduced from the sources with the array removed.
