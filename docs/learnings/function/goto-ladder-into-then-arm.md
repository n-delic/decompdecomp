---
title: Guards jumping into a later then-arm, and masked counters, on an entity-list update
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 297-instruction N64 function (two pool scans filling a table of ten lists, then a pass that draws each listed
entity), sn-n64-281 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Machine-draft best 7210.

A typed rewrite written from the target's assembly was exact on its first compile: one record for the entity, the table
as `Entity *table[10][100]` beside `u16 count[10]`, both pool walks as `for (i = 0; i != 100; i++, e++)` with a `u16`
counter, and an `else if` ladder in which every path makes its own call (cross-jumping merges them). The two page
calls take no arguments and the draw call takes five.

Controlled results (the matched source with one m2c habit put back, app's judge):

- `if (a) { v = 1; goto L; } if (b) { v = 1; goto L; } if (c != 1) { v = 0; L: f(v); } else { .. }`: 2090.
  decomp-repair `goto_ladder_then_else` writes the ladder, 0. On the machine-draft best the same child gave 7210 -> 5535.
- `s32` counters masked `& 0xFFFF` at each read, some inside subscripts, stepped by `for` headers: 320.
  `masked_reads_narrow` declined it before; with subscripts and `for` headers it gives 0.
- The tables as correct byte arithmetic: 100. Stray arguments on the page calls: 160 (`target_call_arity` already
  offers the fix). The pool walks as byte offsets from the entity pointer: 0, so that spelling is harmless.

Not reached: a chain of library families from the machine-draft best (5520 after a run capped at 50 judged variants).
The draft also scales its table address over an unknown type and reads the halfword counts as words; whether existing
families repair those two in sequence was not measured.
