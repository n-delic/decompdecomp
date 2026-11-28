---
title: a forward goto hid the drop of a parameter's local copy
kind: function
compiler: sn-n64-281
evidence: observation
date: 2026-11-28
---

A 106-instruction N64 message queueing function (SN GCC 2.8.1, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`). It remaps its slot argument, then queues a message unless a flag local was cleared. The saved
m2c draft scored 3505.

The source changes, scored with the app's judge: byte reads for two slot-indexed tables (3120); those tables and the
message declared as typed arrays and a struct, with the stray fourth argument of `osSendMesg` dropped (575); four
byte copies written in index order (485); the element read as `T[i]` instead of through a pointer temporary (270);
the m2c copy `var_s0 = arg0;` removed so the parameter itself is remapped (270 -> 0). With the copy, the flag took
`$a2` and the target has `$a3`.

`parameter_copy_drop` refused that last step because the function had a forward `goto` and the local is written
again. It now refuses only for a jump back (RetroReversing/decomp-repair#2254, in v0.6.474): from the 270 source its
one child is the matching source.

Failed experiments: passing the flag as a fourth argument (1050), a `u8` flag (830), a duration local (415), a `u16`
parameter or a 16-bit compare local (no change). Assigning the flag before the slot copy scored 180 and moved the
slot to `$s1`.

Controlled evidence: the library's tests for the forward and backward `goto`. The scores are from one function. Why
the copy changes the flag's register was not read from the compiler (it has no public source).
