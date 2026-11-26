---
title: A stepped y in a loop whose body is a switch: the row giv without a fence, 525 -> 205 -> 100
kind: function
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 320-instruction N64 options menu (`sn-n64-281`, default `-mfp64`) drawing three rows: `y = 0x50;` before
`for (; i < 3; i++) { put(0xA0, y); switch (i) { case 0: ... break; case 1: ... break; case 2: ... break; } y += 10; }`,
the arms updating a two-word settings table. The target's entry block issues the table's `lui $s6` second in the
prologue and `addiu $fp, $zero, 0x50` (y) beside the cosine chain; ours had them the other way round.

The source change, and the measured result before and after (scores, the judge used):
`y` as a giv of a counter: `y = i * 10 + 0x50;` (or `y = row * 10 + 0x50;` with `row = 0; ... row++;` last in the
body) and no `y += 10`: 525 -> 205. Then the app's local pass took `switch_variable_split` (the clamp's value as its
own local in both arms): 205 -> 100, recorded. An earlier 270 needed a `do { } while (0)` fence around a call; this
needs none.

Why (read from the `-dS` dumps): `y = 0x50` is a multi-set biv, priority 1 in sched1, with no dependence on the calls it
crosses (`REG_N_CALLS_CROSSED` > 0), so the backward scheduler leaves it, and the other priority-1 free insns, to the
end, highest INSN_LUID first: the loop-hoisted table address (emitted at the preheader, the highest LUID) is picked
early and lands next to its use, `y` floats to the top. sched2 cannot repair it: after reload a hard-register set
depends on the previous call (sched.c's call_used_regs test indexes with the loop counter, so every hard register
counts), so the `lui` stays after the call sched1 left it behind. As a giv, `y`'s `li` is emitted by strength_reduce
after the hoisted address, and the order flips.

decomp-repair `stepped_constant_row_giv` offers it since its switch-body change (PR 1780): a `break` inside a
`switch` or an inner loop leaves only that statement, so the family no longer declines the loop.

Failed experiments that would otherwise be repeated:
`y = 0x50` in the for header, before or after the counter's reset, at the top: 525 (invariant); a pointer local for
the table: 4025-4110 (notes). On the 100: dropping the case-0 `j = D_800B9ECC[0] << changed; D_800B81D8[0] -= j;`
temporary (j is the inner loops' counter) 165: without its two references j, k and x take s2, s1, s0 where the target
has s0, s2, s1; every order of the inner `for`'s initialisers and steps 145 at best; j, k, x block-local 165.

What is controlled evidence and what is one function:
One function; the sched1/sched2 rules are read from the 2.8.1 source and the dumps.
