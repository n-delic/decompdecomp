---
title: Counted searches from m2c, and a result flag set after the call before the search
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 263-instruction N64 actor update at 0x8005247C with three small table searches (found flag, winner index, `break`),
each after a call, in three arms of an `if` chain.

The match, from a clean rewrite of the target (typed record, indexed `for` loops), judged by the app's judge:

- function-scope `found`/`winner`: 200 (the two saved registers swapped in every arm);
- one pair per arm: 100 (`local_web_split` and `arm_private_rename` both offer this child);
- `found = 0;` written after the call it preceded, still in the call's delay slot, so it no longer lives across that
  call: 0 (`init_after_call` moves all three arms in one child).

From the m2c draft (6855) the first gap was the search loop itself: a label whose found case is the `if`'s own arm,
with the step and backward jump in the `else` and a pointer over the table. `goto_backward_loop` writes one loop as
`for (;;)` and keeps the pointer: 5990. All three at once:

- indexed `for` with `break` (`counted_search_index_for`): 3180;
- the same with the pointer stepped in the header (`counted_search_cursor_for`): 3980.

Then `goto_paths_written_out` with the one shared-store label kept (`goto_paths_kept_label`): 1770, against 3480 for
every path written out (the latter measured after `field_width`). From there `m2c_field_record_view` gave 1465 in a
bounded chain of existing families, which stopped at its judge cap; no chain from the draft to 0 was run.

Both new families are in decomp-repair from 0.6.501. These are one function's measurements, not controlled tests.
