---
title: audio thread spill slots and uninitialised flag
kind: finding
compiler: gcc-2.7.2-kmc -O2
evidence: observation
date: 2026-11-27
---

A 241-instruction N64 audio manager thread (`gcc-2.7.2-kmc -O2`, the project's flags), written from the audio library's
known manager shape against a saved best of 1930 that had a wrong statement (a counter increment in an `else` path).

- A clean rewrite judged 826.
- A flag the loop tests as `(left < N) | (restart != 0)` keeps the target's `sltu` only when it is **not** initialised
  before the loop; with `restart = 0;` there the compiler knows it is 0 or 1 and drops the compare (826 -> 65 together
  with the next item). Hypothesis about the cause (nonzero bits); the scores are measured.
- Two locals spilled to the stack take their slots in declaration order: moving the pointer's declaration after the
  size's exchanged the two offsets.
- Two counters set to 0 side by side take their saved registers in declaration order (65 -> 0).

The library reaches 826 -> 71 (`switch_variable_split`) -> 6 (`assignment_run_move`); the last step, two unnamed spill
slots held the other way round, is `spill_declaration_order` for locals not named `spXX` (decomp-repair
`measured_slot_orders`; the swap was exact from the 6 on the 25th child).

Controlled: the spill-slot swap on one source. The rest is one function.
