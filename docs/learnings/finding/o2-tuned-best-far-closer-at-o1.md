---
title: A saved best tuned under -O2 that scores far lower at -O1 marks a function built at -O1
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

State routines of 211 to 881 instructions in a game whose default is -O2 had saved bests of 5890 to 17498 after up to a
hundred attempts each. Judging those same texts at -O1, with no edit, scored far lower for most of them (16165 ->
8451, 17498 -> 9802, 7680 -> 1731, 7091 -> 3275). Plain C written from the target (typed 100-byte records, a real
`switch`, `u16` locals, no fences) and judged under -O1 was exact on the first compile for three of the functions
(211, 236 and, after one swapped comparison, 624 instructions), and after one statement became a chained assignment
for the 881-instruction one. `a[0] = a[1] = 0;` is what reads one address and stores element 1 first.

Tells in the target: the record index (`andi` then the `sll`/`addu` chain) is recomputed at every access, and every
saved register is stored before the first parameter move.

How to find them: judge each open function's saved best at the other -O levels and compare with its own settings.
The match skill's `plain_best.py` (run by `setup.py` at every claim) now does this and prints a `levels` line.

One game, one compiler: the 0.7 threshold for "far closer" is a choice, not a measurement.
