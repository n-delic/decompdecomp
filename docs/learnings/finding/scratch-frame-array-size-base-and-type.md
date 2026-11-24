---
title: scratch frame array size base and type
kind: finding
compiler: KMC GCC 2.7.2 (sn-n64-281), -O2 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: two small N64 MIPS functions (43 and 68
instructions) that call a helper with the address of a stack slot and read one or two floats back. m2c declares the
slots as one scratch array the size of the target's whole frame, `u8 sp[360];`, with a macro per slot at the target's
own offsets, `#define sp118 (*(s32 *)(sp + 0x118))`. Both sat at a saved best of 111 (the frame was 0x20 bytes bigger than
the target's). Compiler: KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`.

The source change, and the measured result before and after (scores, the judge used): three edits, each worse or
unchanged alone in some order, exact together (the app's own `judge-worker`, read-only):
1. slot type follows the target's access: `lwc1`/`swc1` -> `f32`, `ldc1`/`sdc1` -> `f64`, `lw`/`sw` -> `s32`. An `s32`
   read of a float slot compiles to an extra `cvt.s.w` (111 -> 11 on the larger function).
2. array size = lowest saved register's slot - argument area: `u8 sp[360]` -> `u8 sp[328]` (target saves at 0x158, 16
   byte area); the frame's `addiu $sp` then matches (11 -> 3). The smaller function: `sp[48]` -> `sp[16]`.
3. every macro's offset - argument area: the array starts at `16($sp)`, so `sp + 0x118` -> `sp + 0x108` (3 -> 0).
Size alone 8, rebase alone 3, retype alone 100 on the smaller function; sizes 320/336/344 without the rebase: no gain.
decomp-repair `scratch_frame_array` (v0.5.1028) proposes all of it from the target assembly; `prove_chain --families all`
from the saved best 111 and from the reverted draft 211 reaches 0 on both in one step.

Failed experiments that would otherwise be repeated: `stack_gap_padding` (pads a gap, and returns nothing when the
slots are macros no declaration names) and `stack_object_size` (sizes an spNN object, not the scratch array) never
reached 0; `--combine 10 --children 400 --lookahead 3` from 111 found nothing (best child 175 of 177).

What is controlled evidence and what is one function: verified on two functions of one game (exact bytes after
linking). The rule (array ends where the saved registers start; macros rebased by the argument area) is a hypothesis
about GCC 2.7.2's frame layout, not read from its source. Over the project's 2206 functions, 50 drafts carry a scratch
array and the family fires on 15 open functions' saved bests (a best child improves 14 of them, none to 0: those
have other differences).
