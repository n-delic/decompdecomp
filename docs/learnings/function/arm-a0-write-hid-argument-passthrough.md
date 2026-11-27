---
title: another arm's a0 write hid an argument passed through
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 53-instruction N64 slot-opening function, exact under `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`. One arm loads a counter into `$a0`, clears two table slots and jumps to the end; the other calls
a callee after loading only `$a1` (the counter), so `$a0` there is still the function's own first parameter. m2c
drafted `void f(void)` calling `g(counter)`. Symptom: one register row (`lw $a1` in the target, `lw $a0` in ours).

Source changes and the app judge's scores, from the saved m2c draft (315):

- `char` externs with `*(s32 *)(index * 4 + table)` retyped as `s32 table[]` and indexed (`cast_offset_records`, which
  also inlined the index temporaries): 315 -> 5.
- `void f(s32 arg0)` with `g(arg0, counter)`: 5 -> 0. Alone on the draft it was 315 -> 310.

`untouched_slot_passthrough` did not offer the second step: `target_call_arity_repairs.calls` reads in file order, so
the early arm's `$a0` write counted for the later call and no slot looked untouched. Since decomp-repair PR 2001 the
family also reads each call without the registers every route from the entry keeps, and its child on the 5 scores 0.

Spellings that did not match: keeping the index in a temporary in the early arm, `t = counter; a[t] = 0; b[t] = 0;`,
loads the index before the first table's `lui` (20, two operand rows, with the argument fixed). Replacing the draft's
temporaries by the byte-offset expression alone scored 1255.

The scores are one function (controlled: same compiler, flags and judge); the family's behaviour is also covered by a
library test on a homemade reduction.
