---
title: a guard return in the last block is the if/else the target branches on with bnel
kind: finding
compiler: sn-n64-281
evidence: verified
date: 2026-11-27
---

A 40-instruction N64 sound trigger, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The
target ends `lw $v0; bnel $v0, $zero, EPILOGUE; sw $zero (delay slot); li $v0, 1; sw $v0`. m2c writes the tail of
the function's last `if` as `if (G != 0) { G = 0; return; } G = 1;`, which compiles to `beqz`, `li`, `j`, `sw`.

The source change (360 -> 0, the app's judge): `if (G == 0) { G = 1; } else { G = 0; }`. Nothing else changed.

Failed on the way: the same `if`/`else` with the condition kept (`if (G != 0) { G = 0; } else { G = 1; }`) compiles
the same bytes as the draft (360), so the arm order is what matters and a search that only removes the `return` sees
no gain. 80 judged `prove_chain` children of the saved best had no improvement, and no library child had the shape:
`guard_shape` rewrote a guard return only at the function body's top level.

decomp-repair `tail_guard_else` offers both arm orders for a guard nested in tail position; its first child from the
saved best judged 0. One function (the verified case); why `reorg.c` picks the annulled slot is not traced here.
