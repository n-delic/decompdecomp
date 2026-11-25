---
title: SN's assembler writes one .rdata word per li.s, in code order, so float pools are literals in C
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 1,389-instruction N64 actor state machine (sn-n64-281, the flags above). Its float constants sit in a pool at
0x80001550-0x800016E8 that repeats values (42.0f twice, the double 1.0 four times) and has the function's jump table
in the middle. Every load is `lui $at, %hi(D_8000xxxx); lwc1/ldc1 ..., %lo(...)($at)`.

cc1n64 writes float constants as the `li.s` / `li.d` macros; the assembler expands each one into `lui $at` plus a
load of a word it appends to `.rdata` right there, with no sharing between uses. The jump table is written to
`.rdata` where the compiler emits the `switch`, so literals used before the switch's text come before the table and
the rest after it. A pool with repeated values and a jump table inside it is therefore the function's own literals:
write `42.0f` at each use, not `extern f32 D_80001570;` (an extern loads through `$v0`, and the compiler schedules
the load as an ordinary insn instead of a macro).

The source change, and the measured result before and after (scores, the judge used):

m2c's draft declares the pool as 37 `extern f32/f64 D_8000xxxx`. Writing them as literals made the app's judge
(fast_judge, fp32 profile) go from 19121 to 26102: every literal whose instruction was not at the same offset as the
target's was "refers to .rdata+0x.. where the ROM's code has no address", 150 each. With rom/match.py placing the
candidate's own sections by the base the nearby target references vote for (`section_bases`), the same literal
source scores 15462, against 19121 for the extern form (fp64: 15927 against 19541).

Failed experiments that would otherwise be repeated:

Literals before the judge fix: 26102 (worse than the externs, so a search keeps the externs and can never match).

What is controlled evidence and what is one function:

The per-use `.rdata` words and their order were read from cc1n64's `.s` for this one function (37 `li.s`/`li.d`,
the same values and order as the ROM's pool). The judge change is tested in tests/test_literal_pool_placement.py.
