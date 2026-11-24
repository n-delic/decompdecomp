---
title: scheduling flag versus source order
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips2 -O2 [-fno-schedule-insns2]
evidence: observation
date: 2026-11-24
---

What the functions looked like: three small vector-math functions of an N64 game built with GCC (72 bytes each: a few
`lwc1`, `mul.s`, `add.s`, `swc1` per field), stuck at score 50 for hours. Hundreds of source spellings, about 2,850
machine-written variants and 444 direct compiler runs did nothing. Their address range's profile was
`sn-n64-281 -G0 -mips2 -O2 -fno-schedule-insns2`; the target's instruction order is GCC's second scheduling pass,
which that flag turns off. The diff of the saved C under the flagged profile was only moved instructions and the
registers that follow (never another offset, operand or opcode, never a missing instruction).

The measured result, read-only through the app's own judge (`fast_judge.py`, three saved drafts): each scores 50
under the flagged profile and 0 (exact, linked) under `-G0 -mips2 -O2`, the same C and the same compiler. No source
change was needed; no source spelling reaches it, because where the compiler may move an instruction is the profile's.

What reads it (`app/scheduler_fingerprints.py`, plugged into compiler detection (`profile_detection.evidence`, the
deep sweep's judged profiles), the matching preflight and, for review, the exact-trial gate in
`deep_profiles.probe`):
- the diff's two sides hold the same instructions (registers, a branch's moving target and `nop` left out), at least
  one row differing: `order_rows_only`;
- then the same profile with the scheduling passes the other way, at most two judge compiles: a `-fno-schedule-insns2`
  profile drops that flag (then every `-fno-schedule-...`); a profile with neither flag gets `-fno-schedule-insns2`
  and `-fno-schedule-insns` at `-O2` and above, `-fschedule-insns2` and both passes on at `-O1` and below.
The diff rows come from the last diff the app kept for the function, else one judge compile of the saved C under its
own settings (cached per source and settings).

An exact trial is never applied by itself: it is suggested with `review_only`, even when automatic compiler changes
are on (the scheduler changes the order of every function built with the profile; the KMC preflight route for
`kmc_variants` is unchanged).

Count in the game's project (no judging): 4 of its 969 open functions carry a scheduling flag in their own profile
(two `-fno-schedule-insns2`, two `-fno-schedule-insns`, all near-matches), none through an address range; the other
open functions build with scheduling on and are asked for `-fno-schedule-insns2` and `-fno-schedule-insns` only when
their diff is order and register rows.

Failed experiments that would otherwise be repeated: the older flag search only appended `-fno-schedule-insns2`
to a base and never dropped it, so a profile that already had the flag could not be asked to lose it; detection only
listened to the compiler and the FP model.

What is controlled evidence and what is one function kind: three functions of one game, of one shape. Whether every
order-only near-match is a scheduling flag is a hypothesis (the judge decides each case).
