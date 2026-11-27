---
title: A guarded block whose exit tests jump past it was written exit first
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 67-instruction N64 update routine (profile
above). m2c's draft, and the saved best after 17 attempts and 1,163 automatic trials (score 405), had
`if (A < 0x2001U) { if (state == 0xFFFF) { goto block_3; } work; return; } block_3: obj->dead = 1;`. The target
branches with `bne` over a `j end; sb` to the work, where that source gives `beq` to a copy of the store at the end.
The caller also dropped the leading argument of its first call (`a0` is never set before the `jal`).

The source change, and the measured result before and after (scores, the judge used), with the app's judge:

- guard written `if (A >= 0x2001U || state == 0xFFFF) { obj->dead = 1; return; } work;`: 405 to 15
- the leading argument passed on (family `leading_argument_widen`, which already existed): 405 to 390
- both: 0
- the two tests apart (`if (!A) {T; return;} if (B) {T; return;} work;`) with the argument: also 0

decomp-repair `goto_tail_early_exit` writes both guard forms.

Failed experiments that would otherwise be repeated: copying the tail in place of the `goto` only, keeping the work
inside `if (A)`, stayed at 390. All families of the previous library release found no child under 390 (160 judged).

What is controlled evidence and what is one function: one function.
