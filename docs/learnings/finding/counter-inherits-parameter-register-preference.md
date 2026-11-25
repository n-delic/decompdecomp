---
title: A loop counter inherits a parameter's argument register through a staged global store
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp64
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 477-instruction N64 texture-strip blitter (SN GCC 2.8.1, `-G0 -mips3 -O2 -mfp64`, the project default). Both arms of a
`switch (bpp)` run a strip loop with a row counter; the target keeps both counters in `$a2`, the register `y0` arrived in,
and computes `y0 << 2` into `$a2` too before the switch (`sll $a2, $fp, 2`, stored to a global). The saved best (525) had
`D_80107890 = y0 << 2;`, a function-wide `n` counter in the first arm and a block-local `m` counter in the second; the
counters took `$a3` and `$t1`.

Why: global.c `expand_preferences` merges the hard-register preferences of an insn's destination and of a register that
dies in it, and a parameter's entry copy prefers its incoming register. With `n = y0 << 2;` the counter's pseudo inherits
`y0`'s preference for `$a2` (`-dg`: `;; 95 preferences: 6 9`). `prune_preferences` then puts `$a2` in
`regs_someone_prefers` of every higher-ranked temporary that conflicts with the counter (a command word, a rounding term),
`find_reg`'s first pass skips it for them, and the counter takes it.

The source change, and the measured result before and after (scores, the judge used):

`n = y0 << 2; D_80107890 = n;`, the second arm's `m` renamed to `n` and its scratch use of `n` inlined
(`strip = (n * hh) / texh;`): 525 -> 435 with the app's judge-worker (30 register rows). decomp-repair
`global_store_counter_stage` (#1742, 0.6.36) offers it as one child.

Failed experiments that would otherwise be repeated:

The staging alone: 520. The merged counter alone: 600. A block-local counter per arm: 530. Holding `x1 - x0` or `y1 - y0`
in `n` as well: 810-1420 (`n` then conflicts with the incoming `$a2`).

What is controlled evidence and what is one function:

One function; the preference mechanism is read from the `-dg` dumps of these variants and global.c, not a controlled test.
