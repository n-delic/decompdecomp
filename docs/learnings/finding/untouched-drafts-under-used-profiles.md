---
title: untouched drafts under the profiles matched functions use
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

An N64 game whose project default was KMC GCC 2.7.2 `-O2`. A few dozen of its matched functions had a per-function
profile of SN GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. 443 functions were open, most
with a saved best that agents had tuned under the default.

The source change, and the measured result before and after (scores, the judge used):

No source change. A one-off sweep judged each open function's existing C under the SN profile with the app's judge:
18 were exact. 17 of them were the untouched machine draft (15 the stored draft, 2 a draft made fresh with the header's
declarations), not the saved best. Those 17 functions' saved bests scored 200 to 7815 under SN: C tuned for the wrong profile does
not carry over. The same profile with `-mfp32` in place of `-mfp64` then matched 3 more float functions whose targets
use only even float registers (fp_fingerprints). All 21 were saved as function-only profiles; the default and the
compiler ranges did not change.

Rule: when other matched functions of a project use a profile, judge every open function's stored and fresh draft
under it before its tuned saved C, and judge the other `-mfp` flag for a function whose float registers point to it.
The app does this after the background drafts (`app/profile_drafts.py`).

Failed experiments that would otherwise be repeated:

The profile probe (`app/profile_probe.py`) had planned the project's most used per-function profiles since its v3, but
it only runs in the autopilot's saved-source pass and the Deep Permuter, for functions with saved C. In this project it
had finished no pass at all, so none of the 18 was found by the app.

What is controlled evidence and what is one function:

One game, one sweep, judge only; each match was rechecked and applied through the ordinary function-only suggestion.
The demo test (`tests/test_profile_drafts.py`) is a controlled regression test, not a new match.
