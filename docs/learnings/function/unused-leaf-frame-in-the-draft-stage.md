---
title: An empty leaf with a frame: the unused local belongs in the draft stage
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A four-instruction N64 leaf, `addiu $sp,-0x10; addiu $sp,0x10; jr $ra; nop`, under
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

An unused local array of the frame's size (`char buf[0x10];`) took it from 200 (the empty body m2c drafts) to 0.
`leaf_frame_repairs`' `unused_frame_local` writes that child already, one step from the empty draft.

Why it had stayed open: autopilot had tried that child while the function still had the project's default
`gcc-2.7.2-kmc -O2`, where it scores 301 (worse than the empty body's 200: KMC fills the delay slot with the frame's
release). After the function's compiler profile changed, the draft stage redrafted it and judged only the empty body
(200): the family was not one of the cheap draft repairs. It is now (`app/cheap_draft_repairs.py`).

Failed experiment: the same child under the KMC default is 301.

One function; the scores are the app's judge. The attempt history that shows the profile change is this one function's.
