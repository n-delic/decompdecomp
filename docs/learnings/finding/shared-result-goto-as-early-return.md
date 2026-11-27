---
title: m2c's preset result and goto into a later arm's test is an else-if with an early return
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 47-instruction N64 table append (SN GCC 2.8.1). The target ends one arm with a jump to a shared `move $v0,$a3`,
has the other arm's `move $v0,$zero` ahead of its last test, and branches past the copy when that test fails. m2c wrote
a preset result, `goto block_6` from the first arm and the label inside the second arm's last `if`:

    if (kind == 4) { A; goto block_6; }
    v = index;
    if (kind == 8) { v = 0; B; if (n < K) { block_6: v = index; } }
    return v;

The source is `if (kind == 4) { A } else if (kind == 8) { B; if (n >= K) { return 0; } } return index;`
(decomp-repair `goto_join_result_return`).

Measured (exact judge, from the machine draft at 850), one chain: record array rows for the `base + i * 8` store with
the second field at +4 (`member_array_rows`) 500; the `s8` parameter widened (`narrow_widening`) 290; this rewrite 80;
the compound update of one global moved above another's pre-increment (`statement_slide`) 0. Before the new family no
library child of the draft was free of the `goto`.

The record store matters as a struct member access: the word store to a member does not alias the scalar count global,
so the count loaded before it stays in its register for the following `count + 1` (hypothesis: sched.c's
MEM_IN_STRUCT_P rule; not read in this compiler's source).

One function; the rewrite refuses when the result is used anywhere else.
