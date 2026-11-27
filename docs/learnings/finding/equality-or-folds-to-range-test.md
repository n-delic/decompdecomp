---
title: An or of two neighbouring equality tests folds to a range test; separate arms keep both compares
kind: finding
compiler: sn-n64-281 -O2
evidence: observation
date: 2026-11-27
---

Controller Pak save and load routines (113 to 265 instructions), `sn-n64-281 -G0 -mips3 -O2`. The target tests an
error code with `beq 4` then `bne 3`.

`else if (err == 4 || err == 3) A;` compiles to `addiu -3; sltiu 2` (score 1700), and a two-case `switch` to a
compare tree (1410). `else if (err == 4) A; else if (err == 3) A;` matches (0): cross-jumping merges the two arms.

Also seen in the same family: a "not enabled" path that returns the compare's constant 1 is
`if (flag != 1) return 1;` before the body, not `if (flag == 1) { ... } return 1;` (940 and 10 to 0). A record-clearing
loop that tests at the top and jumps back is a `goto` loop with its own index and pointer, index initialised first
(380 to 225 to 0); `for` and `while (1)` are rotated.

Controlled: each step on one function, then confirmed on its siblings. No library family writes the arm split
(searched the installed library's families for it).
