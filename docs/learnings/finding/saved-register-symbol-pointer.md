---
title: saved register symbol pointer
kind: finding
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-27
---

A 479-instruction N64 screen set-up routine, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The target builds two
globals' addresses into `$s6` and `$s7` before its first call and reads members through them much later (`lhu ($s6)`,
`lbu 1($s6)`, `sh $v0, 2($s7)`, `move $a0, $s7`).

The source change: `T *held = &D_x;` and `s16 *held2 = D_y;` at the head of the function, used in place of the names.
2336 -> 0, the app's judge: the frame grows by the two saved registers and the member addresses come from them.
decomp-repair `held_symbol_pointer` offers it (each pointer alone, then together).

Failed experiments: a pointer for every address the target keeps in a saved register (including one built after several
calls for a repeated call argument) scored 465, not 0. `prove_chain` over all families (25 children each) found nothing
better than 2336 before the new family.

What is controlled evidence and what is one function: one function.
