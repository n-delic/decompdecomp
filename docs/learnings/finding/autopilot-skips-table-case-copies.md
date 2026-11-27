---
title: Why a jump table's case copies sat unfound after the family existed, and the draft stage that now judges them
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 90-instruction N64 page setup, the twin-arm neighbour of the one in `jump-table-arm-copies-and-tail-run-pointer`: a
switch whose six-entry jump table sends five entries to one block and one to another, then three word stores into one
record. Draft 2655, with about 400 recorded automatic trials and no better source.

Measured with the app's judge: `case_label_copies` 800 (all stacked labels copied), then `symbol_run_pointer` on the
run after the switch 0. Both library families were enough; nothing was written by hand.

Why the app had not kept the 800 step (read from the project's records):

- Every Deep Permuter trial on the function was recorded before `case_label_copies` was released, five days before it.
- The lane that did return to it afterwards was autopilot's draft stage, three times, with the raw draft and its short
  list of alternatives: one edit each, from a list (`app/cheap_draft_repairs.py`) that did not name the family.

So the draft stage now judges, when the target has a jump table with a shared destination, the draft with a body per
label and then that with the run pointer on top (`case_label_copies`, `case_label_copies+symbol_run_pointer`). On this
function the stage's two candidates score 800 and 0.

A family released after a function's last permuter run reaches it only through a lane that runs again on its own: when
a match shows a family that recorded trials never tried, check the dates before looking for a bug in the family.

The tables come from `switch_tables.read` (MIPS ROM games only). Controlled: the unit tests of the stage's gate and
order. One function: the scores above. Not tested: how often the two candidates help elsewhere.
