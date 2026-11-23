---
title: a middle argument register the target never sets is the caller's own argument, passed on
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 15-instruction N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) that indexes a 12-byte table by its
argument, loads two fields of the entry and calls a callee whose matched C takes and reads three parameters:

    lw   $a0, 0x0($v0)
    lw   $a2, 0x8($v0)
    jal  load               ($a1 is never written: it still holds the caller's own second argument)

Its saved best (score 5) was `void f(s32 arg0)` calling `load(p[0], p[2])`: the second argument went to `$a1` (`lw
$a1, 8($v0)`), one register row.

The source change, and the measured result before and after (scores, the judge used):

The hand match passed an uninitialised local in the middle (`s32 mid; load(p[0], mid, p[2]);`), exact but scaffolding.
The faithful form is the caller's own second argument: `void f(s32 arg0, s32 arg1)` and `load(p[0], arg1, p[2])`,
also exact (5 -> 0, the app's judge through `fast_judge.py`). decomp-repair `untouched_argument_parameter` (0.5.843)
proposes it: `prove_chain.py --families untouched_argument_parameter` from the saved best goes `5 -> 0` in one step,
2 children scored. Its proof that the untouched register is the caller's is `passthrough_argument._incoming_calls`
(straight-line from the entry to the callee's only call, no write to the register); the target must write argument
registers on both sides of it and the C must pass as many arguments as the target writes.

Failed experiments that would otherwise be repeated:

`prove_chain.py --families all` from the saved best found nothing (no child of 64 improved on 5). `passthrough_argument`
offered `arg0` in the first slot (the caller had no `arg1` to pass), which is wrong. From the m2c draft (585) the
family's child scores the same as the draft: the draft's table global (`M2C_UNK D_800B383C` scaled by 4 more) needs its
`char []` spelling and base local first; the argument registers were already right there.

What is controlled evidence and what is one function:

One function. Widening the caller's signature changes only the candidate's own definition; a header that declares the
caller with its present parameters stops the family. The guards are tested in the library
(`tests/test_untouched_argument_parameter.py`). MIPS only (ROADMAP.md).
