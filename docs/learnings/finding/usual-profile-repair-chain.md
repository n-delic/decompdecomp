---
title: a near-match that scores worse under the right profile needs the repair chain run there
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An N64 function of 32 instructions (a random value in a range, written to a field), open under the project's
gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2, built by sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp64 (the profile most of the project's matched functions use).

Saved best: 15 under KMC, 25 under SN. Two edits, measured with the exact judge:

| source | KMC | SN |
| --- | --- | --- |
| saved best | 15 | 25 |
| stray call arguments dropped (`callee_arity_trim`) | - | 15 |
| `v = K; if (c) v = E;` written `if (!c) v = K; else v = E;` (`default_into_then`) | - | 210 |
| both | 10 | 0 |

Under KMC the pair stops at 10: `mfhi $a2` against `mfhi $a1`, a reload spill register picked by `order_regs_for_reload`
(`reload1.c`: the least used hard register), which no tested spelling of the C moved (about twenty: parameter
types, temporaries, reusing the unused parameter). A reload register no C seems to choose, with the opcodes all
right, is a sign to try the other profile with the same source edits rather than more spellings.

Both families already existed. What was missing: `profile_climb.explore` only climbed under a profile that scored the
saved C better (or whose code habits agreed more), and the profile probe only judges the saved C, so the right profile
(25 against 15) was never searched. `profile_climb.explore_usual` now runs `repair_chain` for a near-match under the
neighbours' profile and the project's most used per-function profiles. Replayed from the saved best with the profile as
it was before the match: exact after 113 compiles (`callee_arity_trim` 15, `default_into_then` 0).

One function; the replay is a controlled result for it only.
