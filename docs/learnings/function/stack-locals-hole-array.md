---
title: stack locals hole array
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-26
---

A small N64 helper that fills a halfword triple, calls a routine with the address of a scalar `sp18`, stores
three words at `sp2C..` and calls two more routines with `&sp18`.

Source change and score (the app's standalone judge): the saved best declared `u16 sp10[3]; char frame_pad[8]; s32
sp18; s32 sp2C[3];` (score 4). The match is one object, `s32 sp18[8];`, passed to every call, with the later stores
`sp18[5..7]`: the three words stored after the first call are fields of the same object the call fills. 4 to 0.

Now `stack_locals_one_array` also bridges holes of up to 0x20 bytes between word members, takes the slots from the
`spNN` offsets and drops the `frame_pad` declarations. `prove_chain.py --families all` reaches 0 in one step.

Failed experiments: the contiguous-only rule offered nothing here (the padding sits between the members), so the
function stayed at 4.

Controlled: one function; the 0x20 limit is a bound for search size, not read from the compiler.
