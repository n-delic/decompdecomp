---
title: a look-alike reads another field and builds a constant the donor loads
kind: finding
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-27
---

A 137-instruction camera setup with a 139-instruction matched look-alike (99% similar) in the same N64 game,
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`; the project's default is `-O2`.

The transfer found no names at all: `rom_renames` read the donor's own `j` (a relocation into its own body) as a use
of the donor's name at another address, and one name cannot mean two things. With that jump ignored the donor's C
scored 800 under the donor's profile. Two edits made it exact (judged by the app):

- the donor reads `BASE + 0x210C` and `BASE + 0x2108`, the target 0x210A and 0x2108: the offset literal changes, not
  the base's name (renaming the base moved the second read too);
- the donor passes a global as an integer argument where the target builds 50.0f with `lui`: the literal, and the
  callee's parameter retyped from `s32` to `f32`.

`app/twin_fields.py` offers both, combined, from the autopilot's look-alike drafts and the similar-code pass.

One function. The tests are synthetic.
