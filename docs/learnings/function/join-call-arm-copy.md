---
title: join call arm copy
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-22
---

A 251-instruction N64 sound-selection function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): a switch whose arms
each choose a sound id, then one shared tail `update(play(id, dist, 0xFF), table[(u16) idx], obj + 0x24, 0)`. The saved
best scored 210 with four rows: the target has `move $a0,$v0; lhu $v0,16($sp); sll $v0,$v0,2`, ours
`lhu $v1,16($sp); move $a0,$v0; sll $v1,$v1,2`.

Cause (read from `-dS` output and the scheduler's verbose list, hypothesis for the exact rule in sched.c): with the call
in the join block, the first scheduler fills the lhu->sll load delay with the argument move, which puts the lhu first;
the index pseudo then overlaps the call's `$v0` and is allocated `$v1`. Statement order, fences (`do { } while (0)`),
statement expressions and temporaries did not change it (210, 475, 75). A sibling matched function had the call at the
end of each arm (`result = play(...)` in both arms of its if/else), so the join starts with `move $a0,result` and no `$v0` is live.

Source change: `result = play(var_a0, temp_fs0, 0xFF);` before every `break;` and `goto play;` of the switch, the
join `update(result, table[(u16) sp10], ...)`. 210 -> 0 (the app's compile_and_diff: byte-identical); cross-jumping
merges the identical arm tails again, so the size is the same. decomp-repair `join_call_arm_copy` (PR 1266) makes
this child; `prove_chain` from the saved best reaches 0 with it.

Failed: reordering/temporaries/fences/`*(void **)` entry pointer (210 or worse). One function; a sibling with two arms
agrees, so the shape is probably general for a call result read at a join with a longer argument chain.
