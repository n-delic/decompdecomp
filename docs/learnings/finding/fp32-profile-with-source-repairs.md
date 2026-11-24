---
title: fp32 profile with source repairs
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb, -mfp32 against -mfp64
evidence: controlled
date: 2026-11-24
---

What the functions looked like: four functions of an N64 game built with GCC (72 to 100 instructions: two with six or
seven float and double constants, one with two float literals in a distance test, one a commutative multiply order),
saved at 70, 75, 60 and 60 under the project's `-mfp64`. Under `-mfp32` alone they scored 45, 40, 30 and 35: better, not
exact. Under `-mfp64`, no repair helped (the float literals written inline went 70 -> 60, or 75 -> 70, one swap made it
worse). Under `-mfp32` the same repairs made them exact: every constant inlined (`1.0f` and the doubles), two float
literals, three float literals, and one literal with a swap of `D1 * (x / D2)` into `(x / D2) * D1`. Neither the profile
nor the source change reaches the match alone: the literals only change which registers the constants are loaded into,
and the registers a single-precision value may take are the profile's.

The measured result, from the saved bests with the real judge: `prove_chain.py --flags '<the -mfp32 flags>'` (the
families' children proposed and judged under the profile) reached 0 for all four in one or two best-child steps
(`float_literal_global`, `double_literal_global`, `allocation_lever`); and `profile_climb.climb` (the Deep Permuter's
climb under the flipped profile) with the same context reached 0 in 102, 3, 4 and 6 compiles.

The gap it closed: the climb handed the families the header alone. `float_literal_global` and `double_literal_global` need
the words of the constants (the value the C cannot show: `float_literal_words`), so they never proposed anything there,
and the climb took the first child that improved each round, regenerating and walking the same list again: a replay of
that loop from the same starts stalled at 10, 40 and 30 and spent its whole 400 compiles (the fourth reached 0 in 182
compiles through `allocation_lever`). Now the climb gives the families the local lane's context
(`profile_climb.proposal_options`: tables, callees, entry scope, words) and, under a profile that only flips the
floating-point model, first runs a best-child chain of the registered families under it (`repair_chain`: at most 4
steps of 100 children, at most half the climb's compiles, the FP constant and operand-order families judged first). The
profile is only the judging profile of that search: an exact pair still goes through `deep_profiles.probe`, which rechecks
it and suggests it for the user's review (`review_only`); nothing is applied.

How often it applies (no judging): of 970 open functions of the project these came from, 405 read "even" in
`fp_fingerprints.parity` (every FP register of the target even, where the project builds with `-mfp64`), 25 of them
near-matches under 300 (the detection's reach). The evidence is the target's registers only; the judge decides each.

Failed experiments that would otherwise be repeated: judging the flipped profile on the unmodified saved best finds only
the functions the profile fixes alone; repairs searched under the project's `-mfp64` found nothing for these four.

What is controlled evidence and what is one function: four functions of one game, each undone to its saved best before
the repair and recovered by a generated one with the real judge; it is not evidence about other compilers' FP models.
