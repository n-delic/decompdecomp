---
title: A hand-reduced copy loop (integer cursors, a bound local) was the indexed for loop
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 63-instruction N64 routine that copies a
block word by word through two callees, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The saved best
(160, registers and one missing `move`) was m2c's form: `n = size >> 2; i = 0; if (n != 0) { n = n; dst = base &
MASK; src = base; do { put(dst, get(src)); dst += 4; i += 1; src += 4; } while (i < n); }`.

The source change, and the measured result before and after (scores, the judge used): `for (i = 0; i < (size >> 2);
i++) put((s32 *) ((base & MASK) + i * 4), get(base + i * 4));`, 160 -> 0 on the app's judge. The `move` the target keeps
from the entry test's register to the bound's register is the loop pass's own (hypothesis; the compiler's source was
not read). decomp-repair `integer_cursor_for_loop` offers the three edits as one child.

Failed experiments that would otherwise be repeated: the cursors as indexes inside the guarded `do` loop 160; the same
with a `for` loop and the bound kept in a local 110 (library `assignment_at_use` closes that); the `for` loop keeping
the stepped cursors 1096. `stepped_induction_index` wrote `a & MASK + i * 4` for an integer cursor's start (a
precedence bug that changed the program), and it only ran as a `-fno-strength-reduce` probe.

What is controlled evidence and what is one function: the scores are controlled variants of one function.
