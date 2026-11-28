---
title: A save-space check's raw draft to exact: shared name record, mirrored return value, goto paths, tested value as a number
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: an N64 save-space check of 141 instructions
(`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). Two arms each copy their own 16-byte name into the same
local, look the file up, and finish with the same two tails, which cross-jumping merged. The m2c draft (8978) had the
copies as unaligned words, a second return variable (`v = 1; ... v = ok; return v;`), three labels reached by `goto`
from other arms, and `gOut = (s32) gReady;` under `if (gReady == 1)`.

The source change, and the measured result before and after (scores, the judge used): the app's judge, one chain of
decomp-repair families from the raw draft (decomp-repair PR 2234, v0.6.461):

| step | score |
| --- | --- |
| `unaligned_stack_struct_copy` (now shares one record between the two arms' copies) | 1721 |
| `mirrored_return_value` (the second return variable goes) | 1405 |
| `goto_paths_written_out` (each jump written as the path it runs) | 722 |
| `guard_value_literal` (the tested global read back, written as 1) | 507 |
| `stack_gap_padding` (an unused 0x200 byte array declared first) | 489 |
| `stack_object_size` (the two 4-byte extensions as byte arrays) | 460 |
| `branch_orientation` (one arm's condition the other way round) | 0 |

Failed experiments that would otherwise be repeated: from the draft with only the record fixed (1721), a greedy chain
of the stack and branch families then known stopped at 1567 after 30 judged variants. Keeping
`gOut = (s32) gReady;` costs a reload after the call. A clean rewrite written from the target matched at once.

What is controlled evidence and what is one function: each rewrite has unit tests in the library; the scores are one
function. The steps were proven one at a time and in capped chains (not one uncapped search); the last four steps were
checked again with the released library, whose output equals the judged exact source. That the compiler's cross-jumping
is what merged the tails is a hypothesis from the target's layout, not read from the compiler's source.
