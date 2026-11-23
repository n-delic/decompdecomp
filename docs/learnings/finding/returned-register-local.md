---
title: a function returns the local in the register its epilogue copies into $v0
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 48-instruction N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) that makes an object with an allocator,
keeps the pointer in `$s0`, sets fields through it, calls a set-up function, and ends

    addu  $v0, $s0, $zero
    lui   $v1, %hi(handler)
    addiu $v1, $v1, %lo(handler)
    sw    $v1, 0x9C($v0)

before restoring its saved registers. Its saved best (score 10) returned `temp_s1`, a field it had loaded (`lw $s1,
0xE0($s0)`) and passed to a callee: the compiler copied `$s1` into `$v0` and the last store went through `$s0`, two
register rows.

The source change, and the measured result before and after (scores, the judge used):

`return (s32) temp_s0;` (the function stays `s32`): 10 -> 0 by the app's judge through `fast_judge.py`;
`return temp_s0;` with the implicit conversion is exact too. decomp-repair `returned_register_local` (0.5.841)
proposes it: `prove_chain.py --families returned_register_local` from the saved best goes `10 -> 0` in one step, one
child. The evidence is m2c's naming: a local is named after the register it first lived in, and a saved register holds
one value to the end, so the register the epilogue copies into `$v0` names the local to return. The family reads the
last write to `$v0` before the single `jr $ra` (a register copy, with no call, branch or jump after it) and offers the
closing `return` with that register's local, then the parameter the prologue copied there, then the other pointer
locals, cast to the declared return type so the callers keep their type.

Failed experiments that would otherwise be repeated:

`prove_chain.py --families all` from the saved best found nothing (no child of 143 improved on 10). From the m2c draft
(445) the function is `void` with no return: the family offers nothing there, since a return type change needs the
callers' evidence (`callers.py`, return-type-from-callers.md); the draft's other differences (its unknown callee
prototypes and the global's `char *` spelling) come first.

What is controlled evidence and what is one function:

One function. The guards (one `return`, at the end; no narrow, float or void return; `$v0` not loaded, constant, a
call's result, or followed by a branch) are tested in the library (`tests/test_returned_register_local.py`). MIPS only:
PowerPC's `r3` and x86's `eax` need their processors' epilogue readers (ROADMAP.md).
