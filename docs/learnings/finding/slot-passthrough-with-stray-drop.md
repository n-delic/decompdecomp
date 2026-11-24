---
title: slot passthrough with stray drop
kind: finding
compiler: KMC GCC 2.7.2 (sn-n64-281), -G0 -mips3 -O2
evidence: observation
date: 2026-11-24
---

Shape: an N64 function (54 instructions) tests two flags and, on one arm, calls a three-argument callee after the
branch's delay slot set `$a1` (the constant `0x1E`) and the call's own delay slot set `$a2`, with the caller's `$a0`
still its own; on the other arm it calls a no-argument callee, the same hoisted `$a1` never read. The two arms'
calls of the three-argument callee are one `jal` in the target (tail-merged), two `if`/`else` calls in the C.

m2c drafted the constant as a wrong-slot argument of both: `go((void *)0x1E, 0x100)` where the game wrote
`go(arg0, 0x1E, 0x100)`, and `stop(0x1E)` where the game wrote `stop()` (the callee's prototype `(void)`). Judge rows
at 10 were two register rows (`addiu $a1` against `$a0`, `$a2` against `$a1`).

Measured with the app's judge (blues-brothers-2000, from the saved draft's chain step with score 10):
- the passthrough alone (`go(arg0, 0x1E, 0x100)`): 100, the next call gets a stray `addiu $a0, $zero, 0x1e`;
- dropping `stop`'s argument and its prototype parameter alone (`target_call_arity`): 10;
- both: 0. The pair was only reachable as a combined step (`m2c_refinement>target_call_arity` in `prove_chain
  --combine`), so a plain chain from 10 stalled and the Deep Permuter's pair pass depended on both children ranking in
  its best 30.
- from the m2c draft (350): `call_into_arms` 160, `unknown_table_index` 10, then 0.

Fix: decomp-repair `untouched_slot_passthrough` offers the pair as one child (the other callee has one C call and one
`jal` with fewer written argument registers than the C passes, and the stray literals equal a literal the passthrough
call passes), and counts the calls of an `if`/`else`'s two arms as one site (three C calls against two `jal`s used to
end the family). Over the project's 1645 m2c drafts it fires on 6 (as before the change), the combined child on
exactly this one; on the final sources of 1946 functions it fires on none.

Failed experiments that would otherwise be repeated: none; the combined step found by `prove_chain --combine 10` had the
same edits.

What is controlled evidence and what is one function: the 10 -> 100 -> 0 numbers are one function (observation); the
fire counts are from the saved drafts of one game, unjudged.
