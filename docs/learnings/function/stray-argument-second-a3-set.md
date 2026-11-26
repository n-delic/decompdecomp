---
title: A stray call argument reorders another call's setup (the first scheduler's birthing launch)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 182-instruction loader with nine saved registers and its first parameter spilled to its home slot. It was at 120
with four reordered rows in the block before a four-argument call: the target computes the table address
(`lui/addiu $v0`) before `lw $a1, 0x60($sp)` (the argument reloaded) and `addu $a3, $s5, $s2` (the fourth argument,
also stored through the table). The cause was a *different* call further down: m2c gave a callee a fourth argument
(`count`) its matched definition `(Model *m, s8 (*box)[4], Tri *tri)` never takes.

How it works (read from the `sched` and `sched2` dumps, `passes.py --sched-verbose` and the `-dR` notes; the SN
build has no public source, so the rule is GCC 2.8's sched.c as it appears to behave here):
- The first scheduler works backwards and boosts a "birthing" insn: one whose destination is live and set once
  (`REG_N_SETS == 1`). Hard registers count too. With the stray argument, `$a3` was set twice in the function, so the
  call's `$a3` copy was not birthing. The address chain was, so it went right before the store, and the fourth
  argument's add stayed earlier.
- The second scheduler breaks priority ties by the order of its input, the later insn first. So the first scheduler's
  order decided the final one.
- With the argument dropped, `$a3` is set once. Its copy is launched first, the add follows, and both schedulers give
  the target's order.

The source change, and the measured result before and after (scores, the judge used):

`func(buf, *p9878, *p80E8, count)` with prototype `(u8 *, s32, s32, s32)` became `func(buf, *p9878, *p80E8)` with
`(u8 *, s32, s32)`: 120 -> 0 (`fast_judge.py`, then `compile_and_diff`). Dropping the other stray argument the
signature check named (a zero-parameter callee given `arg1`) as well scored 220: that value is used by the target.

Failed experiments that would otherwise be repeated:

About 50 spellings of the store, the address and the argument all scored 120, or worse with their registers moved:
an index local, a pointer local, the stored word as the argument, the store inside the argument list, a temporary
for the value. So did pointer locals shared with other stores (which made the address non-birthing: 250-1545) and a
base local shared with other tables (285-1285). The scheduler model above says none of them could work while `$a3`
had two sets.

Why it went unseen: `call_signatures` (signatures.py) and decomp-repair's `callee_arity_trim` both read a parameter
list without parentheses, so the callee's `s8 (*box)[4]` parameter hid its definition. Both are fixed. The library's
family now also trims m2c's prototype in the source, without which the dropped call does not compile.

What is controlled evidence and what is one function:

One function. The birthing and tie-break readings come from this compile's verbose scheduler notes.
