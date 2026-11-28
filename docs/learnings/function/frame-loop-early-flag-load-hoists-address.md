---
title: A tested global read above two stores hoists a stored global's address and moves reload registers everywhere
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: controlled
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 377-instruction N64 frame-update loop
(`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). A rewrite from the target's assembly (else-if chain,
`for` loop, array externs, a 3-byte struct copy) went 17423 -> 865 in one step, and swapping one if/else pair's arms
865 -> 605. The remaining rows were all registers: the target's `lui $t0`/`lui $t1` for the high half of one state
global where ours had `lui $v0`/`lui $a0`, and a block-move's scratch registers rotated after it.

The source change, and the measured result before and after (scores, the judge used): `state = 1; dirty = 1; if (busy == 0)`
became `staged = busy; state = 1; dirty = 1; if (staged == 0)`: 605 -> 0 (the app's judge). The `-dL` loop dump says why:
the state global's address was "life 7, savings 2 not desirable" in a 271-insn loop and became "life 9 ... moved" once
the flag's load sat inside its lifetime. A hoisted address that gets no register is rematerialised by reload, which is
the `$t0`/`$t1` spelling and also shifts reload's round-robin for every later spill register. decomp-repair
`global_guard_load_early`.

Failed experiments that would otherwise be repeated: only swapping the two stores scored 20 (the address is hoisted but
the stores come out in the wrong order).

What is controlled evidence and what is one function: the dump before and after is controlled on this function; the
family's reach beyond it is untested.
