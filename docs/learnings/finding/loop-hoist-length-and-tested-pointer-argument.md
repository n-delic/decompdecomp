---
title: Unread counters stop loop.c hoisting a guarded call's addresses, and a tested pointer left in $a1 was an argument
kind: finding
compiler: sn-n64-281
evidence: one function, with controlled variants
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 76-instruction N64 loop over a table of
names (SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). It skips blank names, calls a
retail stub under a global guard, then loads the name. The saved best held two more saved registers than the target
(the guard's `lui` and the stub's string argument were moved in front of the loop), reused the name's address after the
guard, and read the tested name pointer into `$v0` where the target has `$a1`.

The source change, and the measured result before and after (scores, the judge used):

- The loop held 36 real insns at loop time (`-dL`). Four `u16` counters in a new `else` of the guard and at the end of
  the body made 49 (one address still hoisted, 601); five made 51 or more and both stayed in place: 1548 to 10. `u8`,
  `s16` and `u16` counters work; `s32` counters do not (1538), as they are deleted before the loop pass.
- The `else` matters: seven counters without it scored 440, because the name's address is then carried past the join.
- `stub(path);` written `stub(path, names.p[i]);` after `if (*names.p[i] == ' ')`: 10 to 0. Without the counters the
  argument alone scores 1538, no better in a useful way than 1548.

decomprepair's `dead_loop_length` (in `dead_narrow_counter`) and `tested_pointer_argument` reach both from the saved
best: the first child of each (1548, 10, exact).

Failed experiments that would otherwise be repeated: `-mfp32` and other compiler profiles; `do { } while (0)` around
the guard or the body; a ternary, a `return`-in-both-arms inline helper and `status = f(); else status = 0;` instead
of the counted `else`; `for`, `if`/`else` and `while (1)` loop forms; a named pointer local in six spellings (the
register stayed `$v0` or `$a0`); inline helpers for the test, the compare and the element read.

What is controlled evidence and what is one function: the counts and the insn totals are controlled variants of one
function, judged exactly. That the original had counters (for example for a debug print compiled out) is a hypothesis.
The counters and the extra argument change no behaviour and are scaffolding in the matched source.
