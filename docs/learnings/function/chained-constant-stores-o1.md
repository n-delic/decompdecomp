---
title: four stores of one constant were a chained assignment (-O1)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 243-instruction N64 object update, `-O1` (every callee register saved before the parameter is copied). Four adjacent
halfwords of one record in a global array receive 0x40; the target holds the record's address in $a0 and the constant
in $v1, and stores offsets 8, 6, 4 through $a0 and offset 2 through `$at`.

The source change, and the measured result before and after (scores, the judge used):
`r[i].a = r[i].b = r[i].c = r[i].d = 0x40;` (offsets 2, 4, 6, 8) over four separate statements in store order 8, 6, 4,
2: 1360 -> 0 with the app's judge. decomp-repair `constant_store_chain` offers it.

Failed experiments that would otherwise be repeated:
The same C under the project's `-O2`: the byte-clearing `for` loop is reversed into a countdown and the prologue is
scheduled; profile first. A `-O2` search had replaced the loop's counter in its condition with a constant to keep it
from being reversed (a loop that never ends): not a source to keep.

What is controlled evidence and what is one function:
One function.
