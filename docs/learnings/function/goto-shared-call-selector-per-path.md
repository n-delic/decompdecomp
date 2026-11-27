---
title: A goto-shared call with a selector argument written on each path, then the stores into both arms
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 117-instruction N64 entity update built with
sn-n64-281 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. m2c's draft sets a local before `goto`s into the
`else` arm that makes the one collision call, and writes three stores of a value after an `if`/`else` that computes it.
In the target every path sets its own argument registers before jumping to the one `jal`, and both arms of the first
`if` use `$v0` for the value (the draft used `$v1`).

The source change, and the measured result before and after (scores, the judge used), all in the app's judge:
- the call made on every path with the literal, the guard that ended in the jump taking the rest as its `else`:
  1015 -> 25 from the scaffold-stripped draft, 1070 -> 80 from the saved best with its `do { } while (0)` fences
  (decomp-repair `goto_selector_call_split`);
- from the saved best, the fences opened: 80 -> 25 (`fence_open`);
- the three stores copied into both arms of the first `if`: 25 -> 0 (`branch_value_run_sink`, which already existed).

`prove_chain.py` from the saved best with those three families reaches 0.

Failed experiments that would otherwise be repeated: at 25, the local's type (`u16`, `s32`, `s16`, `u32`), chained
against separate stores, a ternary, a block-scoped local and a second local for the later use all compiled to the same
five register rows.

What is controlled evidence and what is one function: one function. That the per-path call is how the original was
written is a hypothesis from jump.c's cross-jumping.
