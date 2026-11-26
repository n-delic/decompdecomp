---
title: A record table whose first word is a pointer is loaded before every store through it
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 128-instruction record state handler (N64),
gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`, though its saved best was built for -O2. The records are 100 bytes
and some offsets are read as a pointer that is then written through: `lw $a0, %lo(SYM)($a0); sh $v1, 0xA($a0)`.

The source change, and the measured result before and after (scores, the judge used): a struct array whose first member
is the pointer, indexed by the global, written once per store, matched first try under -O1 (app judge). From the
first attempt's byte-array form (app judge 2805), `pointer_table_deref` gave 300 and `product_temporary` 0.

Failed experiments that would otherwise be repeated: under -O2 the plain C scored 4085; the saved best's scaffolding
under -O1 stopped at 1520.

What is controlled evidence and what is one function: one function. The profile was applied to this function only.
