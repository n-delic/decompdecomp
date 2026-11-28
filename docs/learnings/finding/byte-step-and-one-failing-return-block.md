---
title: m2c's typed pointer stepped by bytes, and one failing return block inside a free-slot search: the library already chains both
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 19-instruction free-slot search over an `extern char` pool: test the pool pointer, walk records until one is free, return
NULL when the pointer is null or the count ran out (one shared block, placed between the loop and the success path),
otherwise store three bytes and return the record.

The source change, and the measured result before and after (scores, the judge used):

m2c's draft declared `s32 *var_a0` and stepped it `var_a0 += 0x1C` (0x70 bytes in C; the target adds 0x1C) and wrote
`return NULL` twice, which came out as two blocks (360, isolated judge and the app's saved best). By hand: a byte pointer
(210), then `if (p != NULL) { loop; if (n != 0) goto set; } return NULL; set: ...` (0; so is `goto fail` into the
`if (n == 0)` body). Confirmed 0 by the app.

The library already does it: `prove_chain.py --families exit_run_outline,pointer_byte_step,m2c_refinement` from the same
saved best reaches 0 in two steps (`exit_run_outline` 360 -> 150, then `m2c_refinement` -> 0, 10 children judged). No new
repair was needed.

Failed experiments that would otherwise be repeated:

An early `if (p == NULL) return NULL;` before the loop (865): the first return gets its own block after the test.
`prove_chain.py --families all` with `--max-judged 40` stopped at 150 only because of the cap: name the families, or leave
the cap alone.

What is controlled evidence and what is one function:

One function. The saved best had 31 earlier attempts and no match, although a two-step chain was available.
