---
title: a store whose value reads a table sits earlier than m2c wrote it
kind: finding
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 129-instruction N64 spawner that builds a linked chain of records, SN GCC 2.8.1
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Each record gets
`size = (u32) (SizeTable[type] * K) >> 12`. m2c declared the table as one word and added a byte offset to its address,
so C scaled the index twice (`sll` 4 for the target's 2). With that fixed, the rows left were register and order rows.

The source change, and the measured result before and after (scores, the judge used):

Isolated judge, from the app's saved best (2080), each step a library child:

1. `pointer_byte_step_all` (`(&SizeTable)[type]`): 1635.
2. `table_read_statement_slide` (new), the table store above a word clear in the entry block: 1575.
3. The same family, the table store two statements up in the last block: 1025.
4. `statement_slide`, a pointer-field store below a byte copy, once per block: 815, then 605.
5. `statement_slide`, one field copy above a product store: 0.

`statement_slide` refuses every statement with a subscript, so steps 2 and 3 were offered by no family.

Failed experiments that would otherwise be repeated:

Writing the table access as byte arithmetic, `*(u32 *) (type * 4 + (u8 *) &SizeTable)`, with all the orders right
stopped at 670: the target needs the subscript form. A `u16` loop counter in place of m2c's `s32` and its masked
copy made no difference there.

What is controlled evidence and what is one function:

One function. That a pointer store never reaches the table is the family's assumption; the judge decides each child.
