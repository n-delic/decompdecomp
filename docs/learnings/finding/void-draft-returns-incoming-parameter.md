---
title: void draft returns incoming parameter
kind: finding
compiler: KMC GCC 2.7.2 (sn-n64-281), -G0 -mips2 -O2
evidence: verified
date: 2026-11-24
---

What the function looked like: a 14-instruction N64 vector helper (`dst = a + b`, three float adds stored through the
destination). The target is `lwc1/lwc1/add.s`, then `addu $v0,$a0,$zero` (the copy sits ahead of the first store), and
every store goes through `$v0`, the last in the delay slot of `jr $ra`. m2c wrote it `void` (no caller reads `$v0`) with
stores through `$a0`: 115 in the judge, the one missing row being the `move $v0,$a0` and the stores' base register
following from it. Copying `arg0` into locals does not add the copy, and a clobber of an argument reaches 35 only by
moving the other registers (a scaffold, 120 once the return is added).

The source change: declare the function `s32` and end it with `return (s32) arg0;` (or keep the parameter's own pointer
type, `void *` with `return arg0;`; the bytes are identical). 115 -> 0 with the app's judge, `fast_judge.py`.

Rule: when the target's last write to `$v0` before the single `jr $ra` is a copy of `$a0..$a3`, with no call, branch target
or write to that argument register before it, the function returns its parameter; a `void` draft has no way to say so.
`returned_register_local` keeps the declared return type and offers nothing for `void`; decomp-repair
`void_parameter_return` is the other direction (declares the return type and the return from that evidence). Whether a
caller reads the value is the callers' evidence (`callers.py`); a caller that ignores it compiles the same.

Failed or partial: a look-alike neighbour (the same helper with a float parameter scaled instead of a second vector) goes
115 -> 60 with the family, not 0: the `move` lands at the top of the function instead of after the first multiply, a
scheduling difference no return spelling tried changed (pointer or `s32` type, the saved `do { } while (0)` fence kept or
removed). That row is still open.

Controlled evidence and one function: the 115 -> 0 is one function (verified). Over the 2108 functions of one project the
evidence (copy of an incoming argument register into `$v0`) is in about 1% of them (22), 21 already matched with a
value-returning source and one open `void` draft where the family fires.
