---
title: statement moves across calls and fences
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags: N64 functions built with SN/KMC GCC (`-O2 -mips3`) whose only difference from the matched source was where a one-line store or initialiser sat relative to a call, or how many `do { } while (0);` loops enclosed it: a result variable cleared before a call and the load after it, a field zeroed after the call it was written before, a test's call taken into a local above the store that precedes the test, two initialisers fenced TOGETHER in one fence (fencing only the second scored the same as nothing), and an arithmetic statement fenced four deep.

The source change, and the measured result before and after (scores, the judge used): the hoist/sink/condition children of decomp-repair `statement_across_call` and the run/deep children of `statement_fence` were built from them; the first of the two edits of the initialiser-run function took it from 1320 to 980 (the second edit, un-fencing a call, is `fence_open`'s). Judged read-only with the app's isolated judge worker. A counter step in its own fence was already reached by `run_weight_scope`/`loop_weight_scope`, and a declaration-order swap by `allocation_lever` and `spill_declaration_order`: those two cases need no new family.

Failed experiments that would otherwise be repeated: a hoist offered with no call among the statements it crosses is `statement_group_orders`' job and gives the scheduler nothing; moving the pre-fix function of a different case (a draft with `__repair_addr` scaffolding) scored worse alone (255 -> 676).

What is controlled evidence and what is one function: observation, one function per move.
