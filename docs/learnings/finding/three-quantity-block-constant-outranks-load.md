---
title: In a block of three local quantities the compare constant outranks the load; weigh the load with scopes
kind: finding
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:
A 182-instruction N64 state machine (`sn-n64-281`, per-function `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp32`) at 15: `if (D_X == 1)` after a call, the target `lui $v0; lw $v0; li $v1, 1; bne $v0, $v1`,
ours with the load in `$v1` and the constant in `$v0`.

The source change, and the measured result before and after (scores, the judge used):
`do { do { mode = D_X; } while (0); } while (0); if (mode == 1)`: 15 -> 0 (depth 1 stays 15; depth 3 is 0 too).
Recorded matched with the `-mfp32` trial applied through `suggest`. The scopes are scaffolding.

The rule (local-alloc.c in 2.8.1 and the `-dl` dump): the block holds exactly three local quantities (`lui`, `lw`,
`li`), and `block_alloc` orders three with a fixed exchange over `qty_compare (0, 1)` and `qty_compare (1, 2)`
(quantity numbers, not positions). Priority is `floor_log2 (refs) * refs / (death - birth)`: the constant, born one
insn before the branch, beats the load and is allocated first, taking `$v0` that the dying `lui` frees. flow.c weights
every reference by loop depth, so the load in a two-deep `do { } while (0)` has refs 4 (priority 2 against 1), the
exchange keeps birth order, and the load takes the `lui`'s register.

decomp-repair `compare_load_weight` (0.6.22) offers depths 2, 1 and 3.

Failed experiments that would otherwise be repeated:
`!=` with arms swapped, a switch, `1 == x`, unsigned extern, a plain local copy, `1U`, `!(x ^ 1)`,
`D_X - 1 == 0`, flag/idx locals: all 15 or worse.

What is controlled evidence and what is one function:
One function; the exchange is in the compiler source, the priorities in the dump.
