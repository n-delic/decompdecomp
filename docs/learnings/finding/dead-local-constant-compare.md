---
title: dead local constant compare
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:
A 21-instruction N64 table-entry predicate (`a != 4 && b != 0 && c < 0` over a 40-byte-stride table, three early
returns of 0), KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`. Every row matched except the compare constant:
the target has `addiu $v1,$zero,4; beq $a0,$v1`, ours `addiu $v0,$zero,4; beq $a0,$v0` (score 10).

The source change, and the measured result before and after (scores, the judge used):
Inserting `result = 4;` before `if (index == 4)` and comparing with `result` (the local the next statement reloads
from the table) took the saved best from 10 to 0 (app judge worker, then `compile_and_diff`). `result` is then a
multi-set pseudo that lives across blocks, so global allocation gives it `$v1` after the local allocator has used
`$v0` for the temporaries; the lone constant is a local-allocator quantity and takes the first free register,
`$v0`. That split is a hypothesis from reading local-alloc.c/global.c; the scores are the evidence.
decomp-repair family `dead_local_constant_compare` emits it (decomp-repair PR 1203).

Failed experiments that would otherwise be repeated:
A fresh `s32 four = 4;` at entry or before the load (cse folds it back to the literal, 10 stays 10); `result = 4;`
at the top of the function instead of beside the compare; the same through `__repair_result_2` (10); returning the
result through a pre-set `u32 r = 0` (allocation moves everything, 255-265); an `||` chain or a `&&` chain for the
early returns (255-265). The match also still carries the saved best's dead `if (result + result == 2)` with
identical arms, which gives `result` its multi-use pseudo: without it the same edit scores 630.

What is controlled evidence and what is one function:
One function. The family is proved only by that function's 10 -> 0 and by unit tests of its safety rules.
