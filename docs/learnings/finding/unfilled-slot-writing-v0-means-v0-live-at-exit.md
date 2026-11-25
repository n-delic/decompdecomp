---
title: a delay slot left as nop where a write to $v0 would fit means $v0 is live at the exit
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: observation
date: 2026-11-25
---

What the function looked like: three animation-state handlers (a callback that tests a helper's result and stores a function
pointer): `beqz $v0, exit; nop` followed by `lui $v0, %hi(f)`, with the taken path reaching `jr $ra` without a write to `$v0`.
m2c drafts them `void`.

The source change, and the measured result before and after (scores, the project judge): declare the function `s32` with no return
statement (it falls off the end with `$v0` as the helper's result). reorg will not move `lui $v0` into the slot of a branch whose
taken path leaves `$v0` live (it would clobber the return value there); in a `void` function it does. Each of five such handlers
improved by one row (865 -> 765, 8596 -> 8496, 575 -> 475, 980 -> 880); three matched as `void` and did not change (the shape
alone does not decide the type). Library: `nonvoid_no_return` (decomp-repair 0.5.1045), one child.

Failed experiments that would otherwise be repeated: adding a `return` of the helper's result (the function then has the value
on every path, a different epilogue).

What is controlled evidence and what is one function: observation, eight saved drafts of one project (five s32, three void); no
open function of that project fires it.
