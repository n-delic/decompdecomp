---
title: cursor postfix on typed record pointer
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:
A 10-instruction N64 display-list command writer (SN GCC 2.8.1, `-G0 -mips3 -O2`) that loads a global command cursor,
steps it by 8 bytes and writes two words at the slot it loaded. The m2c draft and every saved best spell the cursor as
an untyped pointer with byte arithmetic: `void *t = G; G += 8; M2C_FIELD(t, s32 *, 0) = ...; M2C_FIELD(t, s32 *, 4) = ...;`.
The target keeps the loaded value in the register the sum is built from; the draft's spelling copies it first
(`move $t0,$v0`) and sums into a second register (110: two register rows and one missing `move`).

The source change, and the measured result before and after (scores, the judge used):
The global is a pointer to an 8-byte word pair and the step is the postfix form in the initialiser:
`typedef struct { u32 w0; u32 w1; } Rec; extern Rec *G; Rec *t; t = G++; t->w0 = ...; t->w1 = ...;`. 110 -> 0 (the app's
isolated judge). The `extern` redeclaration is local to the function's own source; a header-declared global must be
re-checked against the matched functions that use it. decomp-repair `cursor_record_postfix` (0.5.1024 and later) offers
it: `prove_chain.py --families all` on the saved best reaches 0 in one step. The same family, measured on a project's
other open N64 functions with that shape (nothing saved): 130 -> 0, 135 -> 0, 537 -> 12, 710 -> 420, 380 -> 245, 940 -> 810.

Failed experiments that would otherwise be repeated:
All 110 or worse: the copy before the increment on `void *` byte arithmetic (the copy folds away); `G = t + 1`, `G++;` after
the copy and `G += 1;` on the typed pointer; `do { } while (0)` fences (770). No other registered family reached it
(`increment_old_value_copy`, `counter_increment_split`, `counter_array_push`, `field_struct_view`; `--families all
--combine 10 --children 400`): none retypes the global, and `field_struct_view` redefines the access macro, not the cursor.

What is controlled evidence and what is one function:
One function reached 0 by hand and by the family; two more open functions reached 0 with it, so the shape recurs in
display-list writers. Why the typed postfix form allocates differently is a hypothesis (the old value is the postfix
expression's own value, so no separate copy is left to coalesce), not confirmed in the compiler source.
