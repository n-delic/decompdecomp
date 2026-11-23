---
title: a call m2c left an argument short takes the local that already holds the value the target passes
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 37-instruction N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) that clears a field after releasing it:
the target loads the field into the argument register, tests it and passes it on,

    lw    $a0, 0xC4($s0)
    beqz  $a0, .L
    ...
    jal   release          (a callee that takes one pointer)

while its saved best (score 10) read the field into `temp_a0`, tested it, and called `release();` with the m2c
declaration `M2C_UNK release(void);`. With no argument the loaded value was free to take `$v0`: two register rows (`lw
$v0, 0xc4($s0)` and the `beqz` on it).

The source change, and the measured result before and after (scores, the judge used):

`release(temp_a0);` with the declaration given the parameter: 10 -> 0 (the app's judge through `fast_judge.py`). Both
`M2C_UNK release(s32);` (the local's own type) and `void release(void *);` (the callee's matched definition) are exact.
decomp-repair `missing_call_argument_local` (0.5.838) proposes it: `prove_chain.py --families
missing_call_argument_local` from the saved best goes `10 -> 0` in one step, 2 children scored. The family's evidence
is the target's last write to the missing argument register before the `jal`: a load at the offset the local's last
assignment reads (so a local written again in between is never offered), or for a copy or computation a local m2c named
after that register. The callee's declaration takes the callee's own matched definition's parameters when the app
supplies them (`callees`, the registry's new optional context: `capture_followup_repairs.callee_evidence`), else the
local's type. Constant and global arguments stay `target_call_arity`'s step.

Failed experiments that would otherwise be repeated:

`prove_chain.py --families all` from the saved best found nothing (no child of 120 improved on 10): `target_call_arity`
adds constants only, `passthrough_argument` the caller's own untouched parameters, and `zero_argument` needs the
matched C to say `void(void)`. Calling with the argument but leaving the `(void)` declaration does not compile; an
unprototyped `()` declaration compiles but was not tried as a match.

What is controlled evidence and what is one function:

One function. The family's guards (call counts paired with the target's `jal`s, the offset or register-name evidence,
arity agreement with the callee's matched definition and the header, no varargs) are tested in the library
(`tests/test_missing_call_argument_local.py`); x86 and PowerPC call setups are not read (ROADMAP.md).
