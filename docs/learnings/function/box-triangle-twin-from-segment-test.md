---
title: A 1292-instruction box-against-triangles test from its segment twin
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 1292-instruction N64 box-against-collision-triangles test (SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp32`, recorded as fp64). It is the twin of a matched segment-against-triangles test: the same object
cache, vertex loop, plane normal and hit record. On top of that it has a vertex-inside-box test, a clip of each triangle
edge against the 6 box faces (`j = (k + 1) % 3`), and the 12 box edges chosen by an if-chain (`a.x = b.x = p0.x;` style
pairs) and tested against the triangle like the segment.

The source change, and the measured result before and after (scores, the judge used):

The twin's matched source extended by hand from the assembly: 74699 (m2c draft under fp32) -> 13996. Then, with the
app's judge-worker:
- the three vertex-inside-box tests written out instead of as a `k` loop: 2850;
- the overflow message as `extern char D_80007D14[]`, the string the twin's pool holds (decomp-repair
  `target_string_symbol` covers it): 1200;
- each comparison written with the vertex first (`tv[0].x > p0.x && tv[0].x < p1.x`): the target loads the vertex
  first;
- one `len` local per `sqrtf` site: 145;
- the edge-clip ratio `r` written as the later `t` (one float variable for both): 0.

The last step is a `float_local_merge` child, but that family read declarations up to the block-scoped `f32 sign;` and
so never saw `r`. Fixed in decomp-repair #1747.

Failed experiments that would otherwise be repeated:

`t = d0 / (d0 - d1)` (1406), and `d1` reused for `t` (8527: the frame shrinks by 0x10).

What is controlled evidence and what is one function:

One function. Every step was judged against the whole function.
