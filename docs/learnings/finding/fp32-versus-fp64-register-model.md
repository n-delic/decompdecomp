---
title: fp32 versus fp64 register model
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb, -mfp32 against -mfp64
evidence: observation
date: 2026-11-24
---

What the functions looked like: five small float functions of an N64 game built with GCC, stuck as near-matches
(25, 10, a few rows, 380 and 505). The diff of the best C was only FP register rows: the target's single-precision
loads and operations use even registers (`$f0`, `$f2`, `$f4`, `$f12`) where the compile used odd or other ones, and a
double constant sits in `ldc1 $f2` in the target against `$f1` in ours. The profile the compile used was SN GCC's own
default, `-mfp64` (the register file is the profile's: no source spelling changes it).

The measured result: the same saved C under `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mfp32` instead of `-mfp64`:
25 -> 0 and 10 -> 0 (exact, judged by the linked verdict), a third was applied by hand, and two more went
380 -> ~230 and 505 -> 120. None of the source changes was needed.

What reads it from the target, without compiling (`app/fp_fingerprints.py`, plugged into compiler detection
(`profile_detection.evidence`), the matching preflight and the deep climb):
- every FP register of the target's single-precision loads, stores, moves, operations and double constants is even
  (at least two distinct registers, one in an operation): `-mfp64` allocates odd ones, so the other model is tried;
- the reverse: an operation on an odd single or a double in an odd register exists only under `-mfp64`;
- the diff of the best C is only FP register rows (the same instruction with other `$fN`).
Read-only check on the saved targets: all five read "even" and ask for `-mfp32` on SN GCC's `-mfp64` default; the
KMC `-mfp32` project profile asks for nothing, as it should.

An exact trial is never applied by itself: the pair is suggested with `review_only` even when automatic compiler
changes are on (the floating-point model changes every FP register of a function's build).

Failed experiments that would otherwise be repeated: source spellings and register hints on the near-matches did
nothing; the existing refinement dropped `-mfp64` only together with `-mips2`, and only after another toolchain had
scored better, so the same compiler with `-mips3 -mfp32` was never judged.

What is controlled evidence and what is one function: the exact results are two functions and a hand-applied third;
the parity reading is checked on five targets of one game. Whether every `-mfp32` build allocates even singles is a
hypothesis (the judge decides each case).
