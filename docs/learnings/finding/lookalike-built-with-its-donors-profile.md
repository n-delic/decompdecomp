---
title: a look-alike in the same game is built with its donor's per-function profile
kind: finding
compiler: KMC GCC 2.7.2, -G0 -mips3 -mgp32 -mfp32 -O1 beside an -O2 default
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: an N64 game with 360 per-function compiler
profiles (mostly `-O1` beside the `-O2` default, a few SN builds). Open functions whose instructions are 97-100 % those
of a matched function in the same game (an 81- and a 155-instruction function among them) had been tried by the
similar-code pass with the donor's C and the receiver's own `-O2` only: scores 2115 and 2820, cached as misses. Agents
then matched such functions by hand on the first try by copying the donor and keeping its profile.

The source change, and the measured result before and after (scores, the judge used): the donor's C with the receiver's
names, built with the donor's profile (`-O1`) scoped to the receiver, matched byte for byte under the ROM link judge
(2115 -> 0, 2820 -> 0); the exact recheck then published `-O1` for those two functions alone. On a scratch copy of the
game's database (421 open functions, same-game donors only), the pass before the change matched 0 (every pair cached),
after it 2, both by the donor's profile. The compile-guided constant search (`app/lookalike_profiles.py`) found nothing
to do there: the remaining near misses (scores 100-800 at 99 % similarity) differ in length or instruction order, not
in immediates.

Failed experiments that would otherwise be repeated: lowering the similarity floor from 90 % to 80 % for the same run
added 10 attempts and no match. Changing one literal at a time on swapped constants (`+1`/`+2` against `+2`/`+1`)
makes two branches identical, which GCC merges, so no single edit gets closer; mark every candidate literal with a
distinct value in one build instead (`locate`).

What is controlled evidence and what is one function: two verified matches in one game, judged by the unchanged ROM
link judge; the constant search is covered by synthetic tests only (tests/test_lookalike_profiles.py).
