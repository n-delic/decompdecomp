---
title: override store split
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 (GCC 2.x)
evidence: controlled
date: 2026-11-24
---

A 70-instruction N64 function (a per-frame object update, `sn-n64-281 -G0 -mips3`) whose tail picks a constant by the
sign of a field and stores it. Its m2c draft and the saved best at score 15 spell that as a local set, overwritten
under the test and stored once after the join:

    var_v0 = 4;
    if (M2C_FIELD(arg0, s32 *, 0x34) < 0) { var_v0 = -1; }
    M2C_FIELD(arg0, s32 *, 0x68) = var_v0;

The only differing rows were three register-allocation rows: the local sat in `$v1` where the target has `$v0`.

Source change: the store written in each arm, the local gone.

    if (M2C_FIELD(arg0, s32 *, 0x34) < 0) { M2C_FIELD(arg0, s32 *, 0x68) = -1; }
    else { M2C_FIELD(arg0, s32 *, 0x68) = 4; }

Measured (the app's judge, isolated): 15 -> 0. Other tails on the same start: the ternary store 1005 (branch sense
flipped), the load of the tested field copied into a temporary first 220. None of the 1052 children the library's older
families produce from the 15 improves on it (the best stays 15). The decomp-repair family `override_store_split` (0.5.1006)
produces the match: `prove_chain --families override_store_split` 15 -> 0 in one step, from the saved best and from the
m2c draft once its two unrelated edits are made (the draft chained with every family reaches 40 and stalls on those).

Mechanism (a hypothesis from the registers, not read in the compiler's source): a pseudo set twice and read at the join is
one more quantity for local allocation to rank against the block's other temporaries; with the use in each arm there is
none, and the registers fall as the target has them.

Not covered: a use that is more than one statement, a local read elsewhere in the function, a value with a call or an
assignment, a local that is set in more than two places; a narrow local only folds literals its type keeps.

Controlled evidence for this one function; no second case matched yet. Run without judging over a project's 1,650 m2c
drafts the family fires on 107 (6.5%, 249 children), on 18 of 1,002 matched sources.
