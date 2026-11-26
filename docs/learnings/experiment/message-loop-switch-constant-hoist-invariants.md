---
title: a message-loop switch whose compare constants loop.c hoists: shapes that leave the loop at 55 insns
kind: experiment
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

An 83-instruction N64 message thread: `for (;;) { osRecvMesg(&q, &msg, 1); switch (*msg) { case 1: ...; case 2:
...; case 3: ... } }`. Case 1 tests a mode global: `== 10`, `< 11` (unsigned), then 0 and 1. The target recomputes
the outer switch's constants 2 and 1 (`addiu $v0, $zero, N`) on every pass, where loop.c moves them into `$s2`/`$s0`.

The source change, and the measured result before and after (scores, the judge used):

None matched. `passes.py` gives 55 real insns in the loop. The movables, in order, are: hi of the queue (moved), 2
(moved), 1 (moved), then 3, hi of the mode global and 10 (all "not desirable"), and hi of the counter (savings 4,
moved). Under `threshold - 3` per move, the threshold is between 61 and 63. Keeping the 2 in the loop needs about 60
real insns, or two more moves ahead of the dispatch. An earlier session got every loop row with a dead `long long`
(60 insns, a scaffold).

Failed experiments that would otherwise be repeated:

All of these stay at 55 insns and 4960 (with `u32` for the mode global): `void *msg` read as `*(s16 *)msg`; a
one-member struct `msg->type`; an `s16 type` local; `default: break;` in the outer switch; the mode tests as nested
`!= 10` / `< 11` ifs; a dead counter `n++` (cse deletes it before loop). A real inner `switch` with `case 0`,
`case 1` and an empty `case 10` drops the `== 10` compare (51 insns, 6526). The target's `== 10` therefore survived
jump1, so the original case 10 probably held code that a later pass deleted. Profiles: `sn-n64-272-0001` 11135,
`-0004` 6482, `-0006` 5695, `-mfp32` on 2.8.1 4895 (no change).

What is controlled evidence and what is one function:

The insn counts and decisions come from the loop dump of each variant (one function). The reading of case 10 is a
hypothesis.
