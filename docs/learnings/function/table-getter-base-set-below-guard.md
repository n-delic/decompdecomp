---
title: table getter base set below guard
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 15-instruction bounds-checked table getter (`if (i >= count) return 0; return table[i * 24 + 12];`), under the SN64
GCC 2.8.1 per-function profile above. The saved near-miss kept the table's address in a local set before the guard.
Symptom: the guard's branch delay slot differs, the table's `lui` sits before the compare instead of in that slot, and
the branch offset is one word short.

The source change, and the measured result before and after (scores, the judge used):

One statement moved below the early-return guard (`base = table;`), nothing else: 60 -> 0 (`fast_judge.py`, then the
app's `compile_and_diff`). decomp-repair `exit_guard_assignment_sink` writes it (decomp-repair PR 1979).

Failed experiments that would otherwise be repeated:

- 80 children from 27 existing families, one greedy step: none below 60.
- Writing the symbol back in place of the local (`symbol_base_inline`): 450, though `member_array_rows` on that child
  is also 0. A greedy chain does not take the worse step first.
- Plain `table[i * 24 + 12]` and a record array both matched by hand; pointer arithmetic with `+ 0xC` after the symbol
  did not (450).

What is controlled evidence and what is one function:

The match is one function. That the moved assignment fills the delay slot is an observation; the scheduler and reorg
dumps were not read.
