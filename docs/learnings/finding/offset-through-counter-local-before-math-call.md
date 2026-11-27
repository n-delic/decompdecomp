---
title: A record offset computed before a math call through the shared result local
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 function of 550 instructions, gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`. The target computes
`index * 100` into the saved register that also holds an earlier call's result, before each `__sinf`/`__cosf` call,
and stores through `symbol + offset` afterwards.

The source change, and the measured result before and after (scores, the judge used):

From a typed draft (8570): one `switch` arm moved before another (3965), then `rows[i].pz = f(...)` written as
`n = i * 0x64; *(f32 *) (D_pz + n) = f(...);` with `n` the local that held the call result (525, registers only).
Last, either the offset's shift-and-add steps written into `n` (0, the recorded source) or decomprepair
`local_web_split` on the 525 source (also 0). A chain of `switch_case_order`, four `call_statement_expression` steps
and `local_web_split` reaches 0 from the typed draft (`prove_chain.py`), so no new family was needed.

Failed experiments that would otherwise be repeated:

`n = o->x14 * 0x64` respelled (`*= 0x64`, `* 25 << 2`, `100 *`, `sizeof`) compiles identically (525). An unsigned
`n`: 1305.

What is controlled evidence and what is one function:

One function.
