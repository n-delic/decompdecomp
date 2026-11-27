---
title: display list cursor per command writeback o1
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 171-instruction N64 leaf that loads a palette and a texture into a display list passed as `Gfx **`. Under the
project's -O2 the saved best was 755 behind pointer copies and `do { } while (0)` fences, and plain macro C was 5225.
The same plain C at -O1 is exact (per-function profile).

Sign of -O1 in a leaf, where the prologue rule has nothing to read: each command's constant is built only after the
cursor's write-back (`lw; addiu; sw; lui; sw`), and a float constant for a later conversion is not hoisted above it.

The source shape: `gDPxxx((*p)++, ...)`, the table lookup written inside the second word, `(u16)(u32)` of a float field.
m2c's advance-then-store commands with named locals between the two words are rewritten by `param_cursor_each`
(decomp-repair): refined draft 3170, one child 0 with the width repair and `u8` for the `lbu`-only table.
`gbi_macro_param_cursor` writes a local cursor with one write-back, which scored over 10000 here.

One function is the evidence.
