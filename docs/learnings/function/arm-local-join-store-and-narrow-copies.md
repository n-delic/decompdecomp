---
title: a three-way table choice stored in each arm, and a row index narrowed with its copy
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like and the exact compiler and flags: a 180-instruction N64 menu function (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`, the project's default profile), saved best 110 with register rows only (22 of them).
Two places picked a value from a table, a global or a default global in three arms and merged `(value & ~0xFF) | byte`
into a record field after the join; the target had `lw $v0,table($v0)` (the assembler's `$at` expansion, because the
destination equals the index register), the mask in `$v1` and the byte in `$a0`, the mask loaded in the arm's last
slot. Elsewhere a selected-row byte was copied through two word locals and cast `(u8)` at every use.

The source change, and the measured result before and after (scores through the app's judge, recorded 110 -> 20 -> 0):
1. Write the store in each arm, with the arm's value and byte folded into it and the row pointer left a shared local:
   `*(s32 *)(row + 0x10) = (table[i] & ~0xFF) | state->byte;` per arm. First join 110 -> 60, second 60 -> 20.
   (A second site alone from the 110 source scored 430; the first alone 60.)
2. Declare the selected-row local `u8` and its loop copy `u8` together: 20 -> 0. `u8` on the first alone scored 620
   (the loop's copy became an `andi`), on the copy alone 1490.
The match relies on no scaffolding: both are source spellings a developer could have written.

Why (read in local-alloc.c and global.c; the compiler's own documentation says nothing, so this is a hypothesis
confirmed by the match): with the store after the join the value is a pseudo set in three blocks (global, allocated
after every local one) and the `-0x100` mask a local pseudo of the join that took `$v0` first, so the value could not.
In each arm the value, the mask and the byte are local to one block each, `and`/`or` tie to their dying operand, the
mask lands after the value, and cross-jumping after allocation merges the identical tails (the `j` into the shared
`and`, the mask's `li` in the delay slot). Narrow word locals: a `u8` copy between two `u8` locals is a plain `move`,
a `u8` from a word local is an extension, so the pair must change together.

Failed experiments that would otherwise be repeated: `var = (var & ~0xFF) | byte` assigned back (165); the mask as a
variable set in each arm (1215: sched1 puts a multi-set pseudo's `li` ahead of the load that frees the register the
mask wants); `join_tail_arm_split` (copy with every private name renamed per arm: 1065, the tails no longer merge
because each arm's byte took a different register); reading the nav byte twice and typing the local `s32` (1065, the
frame lost 8 bytes); `u8 var_s1` alone (620).

What is controlled evidence and what is one function: one function, each step scored with the app's exact judge and the
chain reproduced from the saved 110 by `prove_chain.py --families all` (join_tail_arm_inline 110 -> 20, local_width_group
20 -> 0). Library: decomp-repair `join_tail_arm_inline` and `local_width_group` (both declare REPAIR).
