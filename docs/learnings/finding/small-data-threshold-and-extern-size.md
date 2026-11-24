---
title: small data threshold and extern size
kind: finding
compiler: sn-n64-281 (SN GCC 2.8.1) -G0/-G4 -mips2 -O2 -fno-schedule-insns
evidence: verified
date: 2026-11-24
---

A small N64 function (a field read, an integer global and a float global feeding a call, built with SN GCC 2.8.1,
`-G0 -mips2 -O2 -fno-schedule-insns`) sat at 100 for a long time. The target built the float global's address as `lui $at,
%hi(F)` / `lwc1 $f12, %lo(F)($at)` and the integer global's as an explicit `lui $v0, %hi(I)` before `lw $v1,
%lo(I)($v0)`. The compiler's cc1 `mips_check_split` decides which: a global at most `-G N` bytes is small data, which the
assembler expands itself (a float load through `$at`, an integer load through the load's own register); a bigger one
is split by the compiler into an explicit `lui` the optimiser can hoist and allocate. At `-G0` every global is split, so ours
split the float where the target did not (diff: `lui $v0, %hi(F)` extra, `lui $at` missing, the `lwc1` base `$v0`/`$at`).

The change, and the measured result (the app's judge, `fast_judge`, scalar source `extern s32 I;` read `I`):
- `-G0`, scalar: 205 in the reconstruction (100 in the saved best it came from).
- `-G4`, scalar: 110 (the float is small again; the integer is now small too, so ours builds its `lui` in the load's own
  register `$v1`, the target's is `$v0`: `lui $v0 | lui $v1`, `lw $v1, lo($v0) | lw $v1, lo($v1)`).
- `-G4`, `extern s32 I[2];` read `I[0]`: 0. The array is 8 bytes, above the threshold, so the integer stays split while the
  float (4 bytes, `extern f32 F;`) is not. `-G8` needs `I[3]` (12 bytes) and also scores 0.
- `-G0` with the array: 205, the same as the scalar (at `-G0` everything is split anyway); `-G4` alone 110. Both halves are needed.

Failed experiments that would otherwise be repeated: statement order, float literal spellings, `volatile`/`const`
qualifiers, `-mips3`, `-mfp64`, `-mgas` variants: none moved the `$at` row. The scheduling variants of the profile see the
same rows as a reordering (the rows are the same instructions with other registers) and do not help.

What names it: every differing row is a `lui`/load/store row written with other registers, and one symbol's `lui` is `$at`
(or the load's own register) on one side and an explicit register on the other. The target says which way to move `-G`
(target unsplit: raise it; target split: lower it or enlarge the declaration). The Deep Permuter now proposes this
(`app/small_data_fingerprints.py`, decomp-repair `extern_size_widen`) as a review-only trial.

What is controlled evidence and what is one function: the mechanism is read from the compiler's source and checked on this
one function (three profiles, scalar versus array); the rest of the diff-reading heuristic is untested on other games.
Upper bound on reach: of 14,807 open near-matches (score 1 to 300) in the N64 projects on one machine, 1,376 have both
an `$at`-built and a split global in the target (an upper bound: jump tables also use `lui $at`); none was judged.
