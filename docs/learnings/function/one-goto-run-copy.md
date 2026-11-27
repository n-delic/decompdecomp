---
title: A call's constant arguments loaded first need a second copy of the call; per-player result flags in an if chain
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 249-instruction N64 function (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`): an `if` chain
over a player index, each arm computing a flag from `s16` position pairs, then a shared success run (a call, a guarded
array store followed by a call with two constant arguments, `return`). The saved m2c draft scored 10685. A rewrite by
hand with the positions as an array of three-`s16` records and `goto success` per arm scored 2545.

The source change, and the measured result before and after (scores, the judge used):

1. One arm's flag in a local of its own (the target had it in `$a1`, the others in `$a0`): 2545 -> 1300
   (`one_web_split`).
2. A later arm jumps into an earlier arm's identical trailing `if (ok) { goto success; }`: 1300 -> 90
   (`chain_arm_check_goto`). Cross-jumping by itself kept the later copy.
3. The last arm's `goto success;` replaced by its own copy of the whole success run: 90 -> 0 (`one_goto_run_copy`).
   The remaining 90 rows were `li $a1; li $a2` loaded after the store's `lui` instead of before it. With one call,
   hard registers `$a1`/`$a2` are set once in the function and `.sched` shows their loads at the boosted priority
   (`7f000001`, `birthing_insn_p`), which puts them right before the call. With the call written twice they are not,
   and cross-jumping after allocation merges the copies back. The explanation of the boost's condition is read from
   the dumps, not checked in the compiler source (hypothesis).

Failed experiments that would otherwise be repeated:

All 90 again: comma operators, prototypes with `s16` arguments, argument locals, a `static __inline__` helper, a
`do { } while (0)` around the whole block. Worse: the store after the call (365), `volatile` (250 to 410), a fence
around only the store or only the call (245). Copying the run over every `goto` (`goto_shared_tail`): 2290.
`local_web_split` (every web at once) gave 1300 but left step 2 nothing to join.

What is controlled evidence and what is one function:

One function. With the three families, `prove_chain` took the state after step 1 to 0 (1300 -> 90 -> 0).
