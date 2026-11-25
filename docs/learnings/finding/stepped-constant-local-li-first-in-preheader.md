---
title: A constant-stepped loop local keeps its li first in the preheader; a row-counter giv puts it last
kind: finding
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:
A 258-instruction N64 menu that draws nine rows (`sn-n64-281`, its default `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`). The loop over `i` from a global start reads three records off `base + i * 0x20` and a flag
table `T[i]`, and passes a y coordinate `y = 0x37; ... y += 0xE` (stepped at the end of both arms of an `if`/`else`).

The source change, and the measured result before and after (scores, the judge used):
The pointer cursors written as index forms off the counter (`D + i * 0x20 - 0xE`, `((s32 *) T)[i]`): 30 -> 60 with
every register right and only `li $s0, 0x37` out of place (second, after the counter's `sll`; the target has it last,
after loop.c's reduced pointers). Then `y` written as a giv of a zero-based row counter:
`row = 0; ... do { y = row * 14 + 0x37; ...; row++; i += 1; } while (i < n);` with the `y += 0xE` steps removed:
60 -> 0, byte-identical in the app (`compile_and_diff`). `row++` after `i += 1`: 250.

The rule, read in cc1n64.exe's `-dL` and `-dS` dumps (under wibo): a stepped local is a biv of its own; its `li` is
an ordinary insn where the source sets it, before every giv initialisation that strength_reduce emits before
`loop_start`. sched1 gives the preheader insns equal priority and the multi-set biv is not a birthing insn, so
`rank_for_schedule` keeps INSN_LUID order and the `li` stays first. As a giv of `row`, `row` is eliminated ("biv N
can be eliminated"), `y` is reduced, cse2 folds its initialisation to the literal, and strength_reduce emits it in
biv order: record_biv prepends, so the biv whose increment the scan meets last (`i`, when `row++` comes first) is
reduced first, and `y`'s `li` comes last. The reduced increment sits at `row++`; reorg copies it into the `then`
arm's `j` delay slot, which is why the target shows `addiu $s0, $s0, 0xE` in both arms.

decomp-repair `stepped_constant_row_giv` (0.6.20) offers it.

Failed experiments that would otherwise be repeated:
`li` as a plain statement, in a `do { } while (0)` fence (350: loop.c inserts the pointers after the fence), before
the guard (250), right after the counter's start (250), an explicit cond pointer (630-920), a local base pointer (1908+).

What is controlled evidence and what is one function:
One function; the pass behaviour (biv list order, giv emission point) is read from the dumps of that function.
