---
title: loop.c moves a one-use constant until threshold falls below the loop's insn count: measured
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp64
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 516-instruction N64 vertex/quad builder (SN GCC 2.8.1, `-G0 -mips3 -O2 -mfp64`), a 70-insn vertex loop without calls.
The target hoists the three array addresses and `0xFF` but loads `0x80` (one `sb` per iteration) inside the loop; every
spelling so far hoists `0x80` too.

Why: `move_movables` moves a movable when `threshold * savings * lifetime >= insn_count`, with
`threshold = (loop_has_call ? 1 : 2) * (1 + n_non_fixed_regs)` and `threshold -= 3` per register moved. The `0x80` movable
has savings 1 and life 1, after seven moves. Padding the loop with stores to a parameter field (no new movables) measured
the threshold: with 101 real insns `0x80` is still moved, with 117 it is "not desirable", so the threshold after seven moves
is between 101 and 116 (`n_non_fixed_regs` about 60-67). With a call in the loop the threshold halves and the target's shape
follows, but the target loop has none.

The source change, and the measured result before and after (scores, the judge used):

None yet: the evidence is that no local spelling of the store changes it. The original either has ~12 more movables moved
before this one in the loop, or the `0x80` pseudo was moved in a loop processed earlier (`moved_once` doubles
`insn_count`; loops are scanned last first, so that would be the quad loop after it), or the value is not a movable
(a register made by loop optimisation, `REGNO >= max_reg_before_loop`).

Failed experiments that would otherwise be repeated:

`(s16)` casts on the vertex stores, `&D_800BD6C8[base + k++]`, `D_800BD6C8 + base + k++` (2800), `0x100 >> 1`, `-mfp32`
(same loop decisions). Earlier agents: the 24 orders of the vertex-2 colour stores.

What is controlled evidence and what is one function:

The threshold range is a controlled measurement on this function's loop; the explanation of the target is a hypothesis.
