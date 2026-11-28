---
title: fade update from the m2c draft that compares its narrow temporary
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-28
---

A 29-instruction N64 object update, a sibling of the 37-instruction fade update in
`fade-update-compare-before-step`, left open under the project's KMC GCC 2.7.2 setting while that sibling had matched
under `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Under the sibling's profile the app's
judge (run standalone) gave:

- The saved best (an `s32` local, `n - 1` repeated in the `if`): 430, then 0 with `compare_before_step` alone.
- The stored m2c draft (a `u8` local, the `if` comparing the `s8` temporary `t = n - 1`): 630.
  `compare_before_step` offers nothing there. `single_use_inline` writes `n - 1` into the `if` (325), and
  `compare_before_step` on that child is 0. `prove_chain.py --families single_use_inline,compare_before_step` finds
  the two steps with decomp-repair 0.6.477, so no new family was needed.

Failed: under the project's KMC setting the sibling's source form scored 360, worse than the saved best's 135 there.
A worse score under the old setting did not show that the source was wrong: judge a matched sibling's source form
under the sibling's profile.

One function; the full unrestricted chain from the draft was not run to the end (a capped run of 40 children stopped
first), so whether a greedy search takes the inlining step before another family's is not measured.
