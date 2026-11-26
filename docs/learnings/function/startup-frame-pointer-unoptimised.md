---
title: A prologue that copies $sp into $fp is -O0 code
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O0
evidence: observation
date: 2026-11-26
---

An N64 startup function (63 instructions) with an endless polling loop: `addu $fp, $sp, $zero`, its argument stored at
its home slot through `$fp`, and an unreachable `j` after the loop's first jump. Under its range's `-O1` it sat at 1510
after 23 attempts with address-taken-argument and padding-array scaffolding.

Plain C with `while (1) { if (p) p(); }` under `-O0` scored 0. The same C with `for (;;)` scored 400: `-O0` keeps the
`while (1)` loop's own jumps. The m2c draft (`loop_4: ...; goto loop_4;`) scored 400 under `-O0`, and 0 once its label
loop is written as `while (1)` (decomprepair `goto_backward_loop`).

Under `-O2` the plain C scored 3573 and under `-O1` 2027, so no source search under the range's settings would find it.
One function; the brief now names `-O0` for a target with that prologue.

A second function of the same session had the other kind of wrong level: a `-O2` best at 1090 full of scaffolding was
exact as plain C under the `-O1` of the matched functions around it, while its scaffolded best and draft scored worse
there (1465, 9485). Write plain C before comparing profiles.
