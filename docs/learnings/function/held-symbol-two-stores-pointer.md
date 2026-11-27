---
title: two stores through one held symbol address need the pointer assigned inside a do-while(0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 159-instruction menu input routine (a switch on a record's state). The target ends two arms with
`lui $v0; addiu $v0, %lo(SYM); sb $zero, 0($v0); sh $v1, -0x16($v0)`.

Plain switch with pointer locals under `-O1`: 1015, with both stores absolute through `$at`. No change from a plain
pointer local (`u8 *p = &SYM;`), an inline setter or getter, `if (1)` around the assignment, or a struct view
(cse folds the offset back into the symbol). `volatile u8 *p`: 680.

`do { p = &SYM; } while (0); *p = 0; *(s16 *)(p - 0x16) = K;` in both arms: 25 (five register rows), then reusing
that pointer for another address (`dead_pointer_address_reuse`): 0, judged by the app. The submitted source reached 0
another way: `constant_memory_store_scope` then `temporary_hoist` from the plain pointer local.

decomp-repair `held_symbol_store_pointer` writes the first form: plain switch 1015 -> 25 -> 0, saved best
1620 -> 630 -> 125 -> 25 -> 0 (isolated judge, `prove_chain.py`). That the loop note ending cse's basic block is what
keeps the register is a hypothesis; the compiler source was not read. One function.
