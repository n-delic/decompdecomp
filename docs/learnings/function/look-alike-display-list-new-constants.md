---
title: look-alike display list with new constants
kind: function
compiler: gcc-2.7.2-kmc -O1
evidence: verified
date: 2026-11-27
---

A 148-instruction N64 textured-rectangle drawer. Its look-alike (143 instructions, matched, `-O1`) draws 8-pixel
tiles; this one draws 24-pixel tiles from a 192-wide sheet. The matched neighbour's C with its own profile and the
new constants scored 125; folding `t = i >> 4; t *= 24;` into `t = (i >> 4) * 24;` (and the same for `s`) was exact
(`split_read_modify_write` finds it). The saved best under `-O2` was 9565. Check a look-alike's profile before
rewriting; the multiply by 24 changes the instruction count, so the app's literal search over a same-shape donor
cannot make this transfer by itself.
