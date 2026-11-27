---
title: a byte table read wide through index plus table
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 28-instruction N64 flag test (two early
returns, then `table[slot] != 0`). The machine draft declared the table `extern char TABLE[];` and wrote
`return *(s32 *)(SLOT + TABLE) != 0;`, where SLOT is a `u8` global. The target is `lui; lbu SLOT; addiu %lo(TABLE);
addu; lbu 0($v0); sltu`.

The source change, and the measured result before and after (scores, the judge used): `extern u8 TABLE[];` and
`return TABLE[SLOT] != 0;`: 235 -> 0 on the app's judge. Retyping only the cast gives 40 (`SLOT`'s `lui` comes
first), and so does `((u8 *) TABLE)[SLOT]` over the `char` declaration. decomp-repair `asm_table_index_read` makes the
change in one step. `asm_address_read_width` skipped it because the sum names two symbols; the table is the one whose
address the target takes.

Failed experiments that would otherwise be repeated: `*(u8 *)(TABLE + SLOT)` over `char TABLE[]` scores 40, as does
the index-first sum.

What is controlled evidence and what is one function: one matched function. The same child takes two unmatched
siblings from 555 to 320 and from 445 to 210; the second reaches 0 with `guard_tail_return_hoist` after it (isolated
judge). Before the new family, `flag_table_byte_read` then `symbol_address_local` already reached 0 from this saved
best, through a temporary address local.
