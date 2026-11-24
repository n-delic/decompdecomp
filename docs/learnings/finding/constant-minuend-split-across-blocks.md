---
title: a constant minus a call's result plus a step keeps the constant in a local set before an early return
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-24
---

What the function looked like: a 92-instruction N64 screen drawer. After an early-return branch (`if (field < -0xC8) { call; return; }`)
it computes `y = 0xC8 - (helper(index) + 0xA)` and passes `y`, `y + 5` and the constant itself to later calls. m2c writes
the difference as one expression, which compiles with the folded constant; the target builds `addiu rA, $zero, 0xC8` in the
call's delay slot, applies `+ 0xA` to the result and then `subu`.

The source change, and the measured result before and after (scores, the project judge): the front end folds `c - (x + k)`
to `(c - k) - x`, and combine folds constants only inside one basic block. The developer kept the constant in a local
assigned in an earlier block (before the early-return branch), the sum in another, and subtracted:

    h = 0xC8;  if (cond) { ...; return; }  t = helper(index) + 0xA;  y = h - t;

940 -> 690 for that edit alone on the m2c draft (`prove_chain`, first step of the chain `constant_minuend_split` 690,
`cursor_record_postfix` 560, `inferred_record_layout` 440, `argument_passthrough_to_callees` 320, `symbol_address_local`
300). The matched source also stages an index in a local and passes the constant local to the later call; those are other
families' steps.
Library: decomp-repair `constant_minuend_split`, children `constant_minuend_split` (the constant set just before the early
branch) and `constant_minuend_head` (as the first statement); gated on the target's `addiu rA, $zero, c` after a `jal`, an
`addiu` of the step and a `subu` of the two.

Failed experiments that would otherwise be repeated: both children scored the same here (690); the edit without the early
branch between (one basic block) folds the constant back and changes nothing, which is why the family declines a function
with no earlier returning `if`.

What is controlled evidence and what is one function: one function, the effect measured (940 -> 690) and the match verified
by hand elsewhere (the matched source has the shape); over a project's 2,206 functions (nothing judged) the gate passes on
one target and the family fires on that function's draft only. Not covered: a sum with no call, a constant that needs
`lui`/`ori`, a constant minuend inside a loop, and a step on the other side (`(c - x) + k`).
