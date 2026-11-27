---
title: spawn point lost store restored
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 131-instruction N64 routine that counts a
timer down, allocates two child records, picks a type byte from a 17-entry jump table and places itself on a screen
edge. Saved best 1003, SN GCC 2.8.1 `-G0 -mips3 -O2 -mfp64`.

The source change, and the measured result before and after (scores, the judge used): the saved best had *lost a store*:
`field_load_hoist` had put `__repair_hoisted = M2C_FIELD(arg0, void **, 0x48);` before the test and
`__repair_hoisted = temp;` where the code stored `M2C_FIELD(arg0, void **, 0x48) = temp;`. The target has that store in
a switch's branch delay slot. It also carried a `{ long long phantom_wide = x; }` block that made the frame 8 bytes
too large, an `allocEntity(1)` call to a callee with no parameters (the target keeps the constant 1 in `$a0` for a
compare and a later byte store), and m2c's `(s16) -field >> 1`. Cumulative, in the app's judge: phantom block out 995,
literal argument out 875, store back 775, cast out 325, one byte store slid above two others 60, two stores into one
child record swapped 0. The library now does all of these (`phantom_wide_drop`, `callee_arity_trim` on literals,
`lost_store_restore`, `negated_narrow_cast`, `statement_slide`, `record_store_swap`), and `field_load_hoist` no longer
hoists a field that is written.

Failed experiments that would otherwise be repeated: none to speak of. When a target store has no counterpart in a saved
best (a `<` row), look for a hoisted local that is assigned but never read.

What is controlled evidence and what is one function: the dropped-store bug and its fix are covered by library tests;
the scores are one function.
