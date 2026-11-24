---
title: a global cursor stepped after an intervening store is read into a local above it, then stepped and written back
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-24
---

What the function looked like: the 92-instruction N64 screen drawer of `constant-minuend-split-across-blocks`, which ends
by writing a two-word command through a display-list cursor (a pointer global) after restoring a saved field of its record.
After `cursor_record_postfix` the statements are `field = saved; p = G++;`.

The source change, and the measured result before and after (scores, the project judge): the developer read the cursor
into a local before the restore store, stepped the local after it and wrote it back,

    q = G;  field = saved;  p = q++;  G = q;

560 -> 500 (`prove_chain.prove` over the chain's three modules: `constant_minuend_split` 690, `cursor_record_postfix`
560, `cursor_load_modify_store` 500). The target loads the cursor above the store and stores the stepped value back below
it, where `p = G++` places the load at the expression.
Library: decomp-repair `cursor_load_modify_store`, one child per count of preceding plain stores the read is hoisted above
(1 or 2), gated on the target's load of the global's symbol, a store to another address, then a store back to the symbol.

Failed experiments that would otherwise be repeated: the full `prove_chain --families all` takes `cursor_record_postfix`
(560) and then other families' children (440, 320, 300) ahead of this one, because its 560 -> 500 is a smaller step than
theirs; the family belongs in the chain after the postfix step and is found by the registry, not by ranking.

What is controlled evidence and what is one function: one function, the effect measured (60 points) with the match verified
by hand elsewhere; over a project's 2,206 functions (nothing judged) the gate passes on 184 targets and the family, after
`cursor_record_postfix`, fires on the draft and saved best of one open function and on the draft of the function above.
It assumes the intervening stores do not change the cursor global (the target makes the same assumption). Not covered: a
cursor stepped by a pair `p = G; G += K` on an untyped pointer, and a hoist above more than two statements.
