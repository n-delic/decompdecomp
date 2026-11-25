---
title: profile probe lane
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

An N64 game whose project default was SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. About
two-thirds of ~1,000 hand matches needed a profile of their own, and each family had a tell in the target:

- only even FP registers (`$f2`, `$f4`; the diff shows `$f1` against `$f2` rows): the same flags with `-mfp32`;
- a float pool word read through `lui $at; lwc1 $f, %lo(X)($at)`: `-G8` with `-mfp32` (the diff under either flag alone
  shows the other's rows, so neither alone moves the score toward 0; `extern u8 X[]` declarations widen the small externs);
- an epilogue `jr $ra; nop` after an instruction a scheduler could have moved into the slot (SN 2.8.1 always fills it):
  SN 2.7.2 (`sn-n64-272-0001`, `-0004`, `-0006`, and `-0001-as0004` where doubles load with `ldc1`) with `-G8 -mips2 -O2`
  or `-G0 -mips3 -O2 -mgas -meb -mhard-float` (no `-mcpu`);
- libultra / SDK code (`osXxx`, rmon and scheduler blocks): KMC GCC 2.7.2, `-G0 -mips3 -O2 -mgp32 -mfp32`.

The source change, and the measured result before and after (scores, the judge used):

No source change: the saved C is judged under the profile (`app/profile_probe.py`, run by the Deep Permuter after its
profile sweep and by the autopilot's saved-source pass; `Console.profile_probes`, `consoles/n64/profile_probes.py`).
Measured on a throwaway copy of that game's project (its 1,031 per-function profiles are the ground truth), judge only,
nothing applied: of 156 hand-matched functions sampled across the 14 profile families whose C scores non-exact under the
project default, the evidence-only plan (no neighbour vote, which only adds profiles) contained a profile under which the
same C is exact for 139 (89%), with 252 judge compiles in all (1.6 per function; the plan is empty, and costs nothing, for a
function whose target shows no tell: 9 of the 156). Misses were mostly KMC `-O1` with scheduling (`-fschedule-insns*`),
SN 2.7.2 builds with the target's epilogue filled, and a few `-mips2` variants of a library compiler.
Reset to open with the C and score the hand match started from, four functions (one per family: `-mfp32`, `-G8 -mfp32`, SN
2.7.2 `-G8 -mips2`, KMC for a libultra scheduler function) were each found and applied for that function only by the probe
in 3 compiles (25 -> 0, 70 -> 0, 170 -> 0, 230 -> 0), the project default and the compiler ranges unchanged.
On the project's 156 still-open functions with saved C (93 with a plan) the probe found no exact pair: what remains
there has C that is wrong, not a profile (scores in the thousands; hand matching had already taken every function whose
near-match was a profile away). 75 scored closer under a probed profile, 33 by at least a tenth (the ones that get a note
for the next agent) and 5 by half or more: a library function 5,097 -> 930 under KMC, another 7,043 -> 2,970 under SN 2.7.2.

Failed experiments that would otherwise be repeated:

`sn-n64-272-0006` does not compile in the app's judge for this project (`context.h: No such file or directory` from its
preprocessor), so its two probes per function cost a quick failed preprocess each and are planned last. The `-mips2`
profiles of SN 2.8.1 (`-G0 -mips2 -O2`, `-G8 -mips2 -O2`, 137 hand matches) were found by the `-mfp32`/`-G8` probes only
because those functions are exact under either flag (no FP code); an integer-only function that needs `-mips2` has no
tell in its bytes yet, and KMC with `-O1` and scheduling on has none either.

What is controlled evidence and what is one function:

The coverage numbers are one game's labelled data, with the hand-matched C as input: they say the plan names the profile
when the C is right, not that a draft that is still wrong will match under it. The four applications are single functions.
