---
title: a cleared local array between record stores, and loop-held symbol bases with byte stores (two N64 functions)
kind: function
compiler: KMC GCC 2.7.2 -O1 -G0 -mips3 -mgp32 -mfp32 -g
evidence: verified
date: 2026-11-27
---

What the functions looked like (two small N64 functions of one game, both saved at 300):

1. A record initialiser: a call, a byte store to a global, a byte store through a global pointer, then word and halfword
   stores into the record, a `sh $zero, 0x20($sp)` / `sw $zero, 0xC4($v0)` pair three times over (the stack store
   before each record store), frame 0x30. The saved C had `volatile unsigned char pad;` in an `if (1)` block and no
   store to the stack, so the three `sh` rows were missing (300).
2. Two nested counted loops clearing a byte table and marking another (m2c: `*(s8 *)(i + &D_A) = 1;` and
   `p = (s32 *)(i + &D_B); *p = 0;` on `extern M2C_UNK` symbols). The target has no `sll`, both stores are `sb`, and the two
   symbol addresses and the 1 are in registers set before the loops (300).

The source changes and measurements (app judge, `fast_judge.py`, starting from each saved C):

1. The scaffold becomes a real local `s16 sp[3];` cleared one element at a time before the statement that makes the next
   store through a pointer, in the assembly's order (`sp[0] = 0;` before the `0xC4` store, and so on): 300 -> 60, then
   the existing `temporary_hoist` (the global pointer read once into `char *p` after the byte store) took it to 0. With the
   three clears together before the first record store: 180; with the array and the scaffold's `if (1)` kept: 60.
   The pointer local set before the byte store scores 200: the target reloads the global after the byte store, which
   may alias it, so `p` is set after it.
2. `s32 base1 = (s32)&D_A; s8 val; val = 1; s32 base2 = (s32)&D_B;` set in that order before the loop, the sums using the
   bases (integer arithmetic, no scaling), the stored literal read from `val`, and the word pointer `temp2` retyped
   `s8 *`: 300 -> 0. Step by step it does not climb: the word pointer narrowed alone 100, integer bases alone 715 (a `(u8 *)&D`
   base is the same), integer bases and the byte pointer 520; only the bases with the constant held between them is exact.
   The order matters: `val` first 225, `val` after both bases 230, `val` inline 230, no `val` local with the bases 230, bases
   inlined 520, the second address computed after `var_a0 += 1` 60. The integer type of `val` (`s8` or `s32`) does not.

Reusable idea: GCC 2.7.2 `-O1` neither hoists a symbol's `lui/addiu` nor a constant out of a loop by itself; a local set before
the loop in the order the registers were taken is how the game's source holds them. A stack store the target makes and no
volatile byte explains is a local the function writes, and the array's elements are written where the assembly puts them.

Library families: decomp-repair `dead_stack_clear_array` (and `dead_stack_clear_block`) and `word_symbol_loop_bases` (and
`_plain`). `prove_chain.py --families all` reached 0 from the saved C of the first in two steps (`dead_stack_clear_array`
300 -> 60, `constant_store_chain` 60 -> 0) and of the second in one (`word_symbol_loop_bases` 300 -> 0).

Failed experiments: the `sp` elements zeroed through a `volatile` array (also 0, no gain), a plain unused `s16 sp[3]` with the
original stores (300: the stores are what the frame row needs).

What is controlled evidence and what is one function: one function each, matched; both rewrites are checked by the exact
judge, and the narrowing of the second pointer's width is a guess the judge confirms (the library offers it only where a
byte store shares the loop).
