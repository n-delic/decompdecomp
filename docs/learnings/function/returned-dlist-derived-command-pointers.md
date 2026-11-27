---
title: a returned display list with calls in its commands: derived command pointers and printed store order
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An 82-instruction N64 routine that takes a display-list pointer, writes eight commands (a depth-image clear: two of
them hold a call) and returns the pointer past them. Compiler: `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64` (the function's own profile). m2c's draft keeps `t = arg + 8; u = t + 8; ...` locals and prints
the stores in the scheduler's order; it judged 7174.

Source change: every command as `{ Gfx *_g = gfx++; _g->w0 = ..; _g->w1 = ..; }` on the parameter, in address order.
With raw words from the draft's own expressions it judged 250 (registers only); `or_chain_order` (the fill
rectangle's `|` chain) took it to 0. libultra's macros written by hand were also 0 on the first compile.

Permuter: `returned_gfx_cursor` found nothing because it only read stores made directly through the parameter. It now
reads the derived locals back as offsets and sorts the store run by address when the calls keep their order, so the
draft reaches 0 in two library steps (decomp-repair PR 2204).

One function; the sorting rule is a text check, not a proof about aliasing.
