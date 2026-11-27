---
title: Calls to different functions written in the wrong order
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -mcpu=VR4300 -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 103-instruction per-frame update of a record and its child record (`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`). The saved best (480) ran `update(a); update(a->child); draw(a); draw(a->child);` where the
target's `jal`s are update, draw, update, draw. gcc keeps calls in source order, so this is the wrong program and no
register or order-of-values rewrite reaches it. The diff shows it as "reordered" rows around the calls.

The source change, and the measured result before and after (scores, the judge used):

With the app's judge: the opening `do { } while (0)` rewritten (`explicit_loop_backedge`, 480 -> 260), the halfword copy
`new->y = a->y` moved above the byte store `new->flag = 1;` (`statement_slide`, 260 -> 120), and the calls written in
the target's order (decomp-repair `jal_sequence_order`, 120 -> 0). By hand from the plain form: calls 260 -> 140, then
the store 140 -> 0.

Failed experiments that would otherwise be repeated:

120 judged children of the whole library on the saved best moved no call (`target_call_order` reads calls to one
function only). `store_adjacent_gather` makes the store move on the plain form but declines a function that has a
`goto`, such as the one `explicit_loop_backedge` leaves; `statement_slide` still does it.

What is controlled evidence and what is one function:

One function. The family has unit tests for the rewrite itself; the scores are this function's.
