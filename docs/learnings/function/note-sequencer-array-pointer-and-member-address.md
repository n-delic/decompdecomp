---
title: note sequencer array pointer and member address
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 369-instruction audio parameter loop (a
`switch` on the note's type inside a `while`), saved best 140: the prologue copied the first parameter before the
result parameter, a shared `note` local sat in `$s1` where the target has `$v1`, and the constant 1 was hoisted after
a table's address.

The source change, and the measured result before and after (scores, the judge used): the app's judge.
- `s16 *tbl; tbl = (s16 *) D_x;` written as `D_x` at each read: 140 -> 60 (decomp-repair `array_pointer_local_inline`).
- the first parameter copied into a local after `res = arg2;`: 60 -> 30 (`entry_parameter_copy`).
- `note` declared afresh in each arm: 30 -> 0 (`local_web_split`).
The last two families declined the function because it holds `&p->member`, which they read as taking `p`'s own
address; with that fixed `prove_chain.py` reaches 0 from the saved best with the three.

Failed experiments that would otherwise be repeated: 1333 library children of the saved best, none better than 140.

What is controlled evidence and what is one function: one function. That the table's address is hoisted in the order
of the loop's first reads is a hypothesis from the target's preheader, not confirmed in loop.c.
