---
title: walk exit global reload
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

What the function looked like and the exact compiler and flags: a 30-instruction free-list pop (read a global head,
walk it while non-null, then take the node off the list and clear two fields) built with KMC GCC 2.7.2 `-G0 -mips3
-mgp32 -mfp32 -O2`.

The source change, and the measured result before and after (scores, the judge used): `var_v1 = D_head;` written a
second time right after the walk, before the `if (var_v1 != NULL)` that follows it. Exact judge: 210 before, 0 after
(a variant with the reload inside the second `if` was 10). The target has a second `lui/lw %lo(D_head)` there and its
first branch jumps to it; without the reload the compiler threads that branch to the return. The library's
`walk_exit_global_reload` family writes it from the draft (`prove_chain.py`: 210 -> 0).

Failed experiments that would otherwise be repeated: none of the 171 children of the other families improved the draft
(all scored, none better than 210).

What is controlled evidence and what is one function: one function (verified 0). The jump-threading explanation is a
hypothesis. The family checks the target's `lw %lo(G)` count against the source's reads of G when the assembly is given.
