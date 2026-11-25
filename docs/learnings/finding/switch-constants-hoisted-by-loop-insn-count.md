---
title: switch compare constants hoisted out of a message loop by its insn count
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-25
---

On a message loop (`for (;;) { osRecvMesg(q, &msg, 1); switch (*msg) { case 1..3 } }`, SN64 GCC 2.8.1) the target
re-materialises the compare constants (`addiu $v0, $zero, 2; beq $a0, $v0`) where our build hoists `li 2` and `li 1`
into saved registers, while both hoist the queue's `%hi` (`$s3`) and a counter global's `%hi`.

What decides it (loop.c `move_movables`, read in the GCC 2.8.1 source; the numbers from passes.py's loop notes): a
movable is moved when `threshold * savings * lifetime >= insn_count`, threshold starts at 61 and drops by 3 per moved
register, and the switch's dispatch (constants with savings 1, life 1) comes right after the call in insn order. With
55 real insns in the loop: the queue `%hi` moves at 61, constant 2 at 58, constant 1 at 55 (55 >= 55), constant 3 fails
at 52. The target therefore had 59 to 61 real insns at loop time with the same final code.

Spellings that stay at 55 (or fewer) and change nothing: a local for the dispatch value (s16 or s32), message
pointers typed u16*/void*/s32* with casts, an unused `osRecvMesg` result, the inner state global in a local, `x = x + 1`
forms, `!= 10` nesting (53), an `if` chain for the inner dispatch (54), one switch with an empty `case 10` (51), and dead
self-incrementing counters (deleted before loop). A goto loop hoists nothing (worse). Profiles `-mfp32`,
`-fno-strength-reduce` are unchanged; `-O1` and the 2.7.2 builds are worse.

Open: which source adds 4-6 insns that cse1 keeps and combine, cse2 or flow remove after loop. Candidates not yet
tried: accesses whose `%hi` cse1 cannot share (load and store in different extended blocks), narrowing conversions
combine folds into the loads.
