---
title: zero test differences named before if
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 20-instruction leaf of an N64 game: it reads three 2D points of `s16` coordinates and returns 1 when the second or
third equals the first. The target loads all six coordinates and computes three differences before its first branch.
The project judge scored everything below.

The source change: keep the origin in two locals, then compute every difference the compound condition tests against
zero into an `s32` local before the `if`, in the order the condition reads them:

    zdiff0 = p3 - y0;
    zdiff1 = p4 - x0;
    zdiff2 = p5 - y0;
    if (((p2 == x0) && (zdiff0 == 0)) || ((zdiff1 == 0) && (zdiff2 == 0))) {

Measured: machine draft with `s16` temporaries 2045; naming alone 55; naming plus the temporaries declared `s32` 0.
The same draft with `s32` temporaries 1175, naming alone 0. Naming only the last two differences 655. Naming the first
test too (`(p2 - x0) == 0`) was also 0. Plain C with all four differences read straight from the points, with no
origin locals, was 35 (register order only).

Failed experiments that would otherwise be repeated: earlier attempts (best 1560) rewrote the reads as a typed record
and moved them around while every subtraction stayed inside the short-circuit condition. That best also read a pointer
copy before assigning it, so it was not a faithful program.

One function, so the compiler explanation (a short-circuit condition cannot compute later differences ahead of the
first branch, so the game held them in locals) is a hypothesis. decomp-repair `zero_difference_locals` (0.6.493).
