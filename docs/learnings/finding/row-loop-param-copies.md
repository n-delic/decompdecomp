---
title: A row sort with parameters held in locals and a while loop for the rotated goto loop
kind: finding
compiler: SN GCC 2.7.2 -G0 -mips3 -O1 -mhard-float
evidence: controlled
date: 2026-11-27
---

What the function looked like and the exact compiler and flags:
A 60-instruction N64 sort-by-callback routine, 20 before the match. Compiler: SN GCC 2.7.2 -G0 -mips3 -O1 -mhard-float.

The source change, and the measured result:
The goto-labelled rotated loop became `while (V < n) { reset; ... }` and the parameters were copied into locals; `pad[4]` became `pad[2]`. Family `row_loop_param_copies` (`row_loop_while` and `param_copies`) composes to the matched loop body after renaming the copies, tested on a reduced copy. Each part alone stays at 20 (hand finding). The pad size is a guess (two fewer words, or half). Judge not run.

Failed experiments:
None recorded beyond the limits stated above.

Evidence level: controlled.
