---
title: A row address in byte arithmetic matches only as nested record arrays
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: observation
date: 2026-11-27
---

What the function looked like: a 99-instruction N64 menu text builder (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1
-g`) whose entry computes a row address `base + slot_byte * 0x510 + 0xA8 + arg0 * 0xC + 0x39A`, `slot_byte` read
from `base + D_80096B10 * 16 + 0x298A`, `base` the word global `D_80096A0C`.

The source change: `&((View *) D_80096A0C)->r[((Slots *) D_80096A0C)->r[D_80096B10].f].r[arg0]` with typedefs that
put 0x510-byte rows at 0xA8 holding 0xC-byte entries at 0x39A, and 16-byte slots at 0x2980 with the byte at +0xA, was
exact (130 -> 0; 910 -> 0 from a one-statement byte draft). gcc expands an `ARRAY_REF` chain as `((I * S1 + c1) +
base) + (J * S2 + c2)`; byte arithmetic is reassociated by fold whatever its grouping.

Failed experiments: five byte-arithmetic groupings (275 or 585); the rows typed with the slot byte read in bytes
(275); the typed view through a local `s32 base = D_80096A0C;` (5: the last `addu` has its operands swapped).

Evidence: one function, app judge. decomp-repair `nested_record_view` (0.6.371).
