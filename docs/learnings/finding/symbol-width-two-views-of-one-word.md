---
title: A char-array symbol loaded as both lwc1 and lw needs one type per site
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 177-instruction spawn loop over a 0x5C-byte record array. Its refined machine draft declares each field address as
`extern char D_x[];`, so every access is a byte. Three position fields are loaded `lwc1` for one call (a float stack
argument) and `lw` for two later calls (the same words passed in integer registers, to prototypes the draft declares
`s32`). `symbol_access_width` declined: two types, no single answer.

Typing each `*(D + off)` site as the target's access in order (`f32`, `s32`, `s32`), when the accesses and sites pair
off one to one, made the chain from the machine draft reach 0 (`prove_chain.py`, the app's judge): 8341 -> 2391
(`symbol_access_width`) -> 1305 (`narrow_widening`) -> 300 (`guard_temp_sink`) -> 0 (`assignment_at_use`). Without it
the chain stopped at 3090. The change is in decomprepair's `symbol_width_repairs.variants`.

The function was first matched by hand with a record struct, and that source needed `-O1`: its prologue saves every
callee register before copying the parameter, and the same chain run under the function's old `-O2` profile is not
evidence either way.

One function is the evidence for the pairing rule; the unit test in decomp-repair is the controlled part.
