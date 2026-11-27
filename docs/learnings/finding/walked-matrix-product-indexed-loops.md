---
title: a walked 4x4 float matrix product is two indexed loops over f32 [4][4], and the loads take a second argument
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 56-instruction N64 function that converts two fixed-point matrices to float, multiplies them and converts back (the
`guMtxCatF` shape). The draft had `goto` loops over a row cursor (`+= 0x10`), a column cursor (`+= 4`) and a byte offset
into three `char [0x40]` buffers, and passed each conversion only its output buffer. The target's single-precision
values use even registers only, so `-mfp32`, not the profile's `-mfp64`.

The source change, and the measured result before and after (scores, the app's judge):

- The loops as `for (i...) for (j...) d[i][j] = a[i][0] * b[0][j] + ... + a[i][3] * b[3][j];` over `f32 [4][4]`
  buffers: 984 to 533 (decomp-repair `walked_matrix_product`).
- The second argument of each conversion call, the caller's own `$a1` and a copy of `$a2` the target moves into `$a1`:
  533 to 0 (`forwarded_trailing_argument`). The app's proposal path reaches 0 from the saved best in these two steps.

Failed experiments that would otherwise be repeated (same loops, one thing changed):

- `char [0x40]` buffers read through `(f32 (*)[4])` casts: 3959. Flat `f32 [16]` with `[i * 4 + j]`: 3805.
- Byte offsets from `i` and `j` into `char` buffers: 2959.
- The last product written column operand first (as the draft had it): 20.
- `-mfp64`: 60 (12 float register rows). KMC GCC 2.7.2: 2359.

What is controlled evidence and what is one function: all of it is one function.
