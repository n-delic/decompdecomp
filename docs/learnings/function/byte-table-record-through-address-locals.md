---
title: byte table record through address locals
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 59-instruction N64 function (full flags
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`), a `switch` on a state byte that calls one handler,
then three halfwords of the object copied into a 6-byte record chosen by an index byte. The saved best (405) wrote
the table as `extern char D[]`: the first store as a bare `*(index * 6 + D) = u16 value` (one byte, `sb`), the
second through a pointer local and the third through base and index locals.

The source change, and the measured result before and after (scores, the judge used): the app's judge. Typed rows
(`typedef struct { u16 x, y, z; } Row; extern Row D[];` and `D[load].x = ...`) are exact: 405 -> 0. Giving only the
first store its width (`*(u16 *)(index * 6 + D)`) is 405 -> 5; the last row is the second store's `addu` operand
order. decomp-repair `byte_table_record_locals` writes the record array from the saved best, its plain form and its
clean rewrite (0 from each).

Failed experiments that would otherwise be repeated: from the 5-point source, five spellings of the second store
(offset inside the cast, `(s8 *)` sum, pointer local, symbol plus offset) all stayed at 5, so the operand order
follows from how the record is addressed, not from that statement's spelling.

What is controlled evidence and what is one function: one function.
