---
title: loaded value compound state machine
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 217-instruction state machine over a record array (stride 100), with two switches and four float easing updates of
the form `x += (K - x) * 0.4f`. Its matched neighbours were built with `-O1`; so was it.

Matched with plain C under `-O1`, applied per function: typed records for the three arrays, the updates written
compound, and an unused `s32 pad[2]` for a frame 8 bytes larger (`unused_frame_padding` adds it). First plain
candidate: 12 (frame only), then 0.

For the permuter: from a freshly generated machine draft the library stalled at 640 (8 rows). m2c writes each update
`t = x; x = ((K - t) * 0.4f) + t;`, and gcc then loads `x` before the constant (`lwc1` before `mtc1`). With all four
written `x += (K - x) * 0.4f;` the draft chain reaches 0 in 8 steps (family `loaded_value_compound`, decomp-repair
pull request 1909). Three of the four alone scored 160.

Failed experiments: the older saved sources (scores 1315 and up, under `-O2`) and the stored draft, whose switch
`case` labels were missing (it scored 18184 where a fresh draft scores 4362).

Why the compound form loads after the constant is not read in the compiler's source; one function.
