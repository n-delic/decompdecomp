---
title: m2c's rotated two-test scan loop is one while over records; stage X - (Y - K) with its fences opened
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 135-instruction N64 ROM-to-memory transfer
routine, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. It looks an address up in a table of
`{start, end, flags}` records. The saved best had m2c's rotated shape: the entry test with the index folded to zero,
`for (;;) { i += 1; p = ...; if (x <= p->start - 1) continue; break; } while (p->end < x);`, and the found flag set twice.

The source change, and the measured result before and after (scores, the judge used), judged by the app's judge-worker
(the function itself was matched with a hand-written clean rewrite, submitted with `compile_and_diff`; the steps below
are isolated judge scores):

- `while (x < t[i].start || t[i].end < x) i++; found = i < N;` with the table read as records: 1060 -> 160
  (decomp-repair `scan_loop_while`).
- Then `end - (x - 1)` with the subtrahend staged (`u32 span = x - 1;`) and the earlier `do { } while (0)` fence of the
  block opened: 160 -> 0 (`subtrahend_block_stage`). Staging alone: 510. Opening the fences alone: 160.

Failed experiments that would otherwise be repeated:

- The same `while` with the raw `(char *) &t + i * 12` casts instead of records: 455.
- Staging the subtrahend on the saved best before the loop was rewritten: 1415.
- `len = x - 1; len = end - len;` (the result variable as the temporary): 285 on a clean rewrite, where a separate
  local was exact.

What is controlled evidence and what is one function: all of it is one function. The explanation (the record index
gives one pointer per step; the staged local stops the `X - (Y - K)` to `(X + K) - Y` fold) is a hypothesis from the
output, not read in the compiler source.
