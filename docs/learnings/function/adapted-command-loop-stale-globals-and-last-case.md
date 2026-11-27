---
title: adapted command loop stale globals and last case
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 140-instruction N64 command-polling loop with a jump-table switch (ten entries), built with the per-function profile
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Its saved best (3260) was copied from a
matched look-alike in another game.

The source change, and the measured result before and after (scores, the judge used):
Five things the copy had not translated (app judge):
- two globals still had the donor's address names. The target reaches its own through a `lui` held in a saved register,
  so no `%lo(symbol)` names them;
- one callee still had the donor's address name (`renamed_callee_refresh` already paired it: 3260 -> 3110);
- `f(2); .. f(1);` where the target passes 2 and 1 to another function and calls `f` once, with no argument, from the
  switch's tenth case, which the C did not have;
- the call after the loop was missing.
decomp-repair `adapted_body_refresh` writes all of these as one child (1960). The last step was in the arm that had
`A = 0; B = 0; C &= ~1;`: the target needs `C &= ~1;` first (0), the order its preheader builds the three addresses
and the mask. `store_run_order` now moves scalar globals and offers it. `prove_chain.py` reaches 0 in those two steps.

Failed experiments that would otherwise be repeated:
All six orders of the arm's three statements: 1960, 2070, 2100, 2230, 1970 and 0. Each adjacent swap on the way to
the exact order scores worse than the start, so only the move to the front finds it. Renaming only the stale globals
left the score at 3110.

What is controlled evidence and what is one function:
One function. The library tests use made-up assembly and check the children, not scores. That the first-use order
decides the preheader's order is a hypothesis from this diff, not read in the compiler source.
