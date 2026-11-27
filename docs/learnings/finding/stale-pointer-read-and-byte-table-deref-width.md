---
title: a stale m2c pointer read, and a byte table loaded wider through its address
kind: finding
compiler: sn-n64-281 GCC 2.8.1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 133-instruction object update, saved best
1536, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The draft read a pointer local in a late block
that was set to a byte array only inside an earlier arm, and read a `u8`-declared table through a plain
`*(index * 2 + TABLE)` where the target loads `lhu` through the table's address.

The source change, and the measured result before and after (scores, the judge used): with the app's standalone judge,
`prove_chain` from the saved best: `stale_symbol_pointer` 1145, `one_shot_scope` 650, `disjoint_local_merge` 260,
`asm_byte_table_deref_width` 60, `target_call_arity` 0. The two new families are in decomp-repair. A typed rewrite from
the assembly (struct fields, `u16` table, `u32` limit) was also exact on its first compile.

Failed experiments that would otherwise be repeated: `((u8 *) sCounts)[1] -= 1` folds to `%lo(sCounts+1)` (550); the
plain subscript `sCounts[1] -= 1` keeps `lui; addiu` and `1(reg)` (0). The behaviour is observed, not read from the
compiler source.

What is controlled evidence and what is one function: one function.
