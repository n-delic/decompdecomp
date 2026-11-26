---
title: -O1 rather than -O2 read from the target's code alone (not reliable across games)
kind: experiment
compiler: KMC GCC 2.7.2 -O1 vs -O2 (-G0 -mips3 -mgp32 -mfp32)
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

The question: can a function's target bytes say it was built with `-O1` instead of `-O2`, before any C matches, so the
profile probe and the brief could try `-O1` first? On MIPS, GCC 2.7.2's `-O2` adds the two instruction schedulers
(`-fschedule-insns`, `-fschedule-insns2`) and a few CSE/loop passes to `-O1`; delay-slot filling is the same at both.

Ground truth: matched functions of KMC games whose profile is known, each one's matched C judged again under the other
level (the app's own judge, read beside the app). That split the "matched under -O2" set into *needs -O2* and *the same
code at both*: one game had 238 functions that need -O1, 300 that need -O2 and 157 that build the same either way (mostly
under 40 instructions); without that relabelling a third of the "-O2" functions were really either.

The source change, and the measured result before and after (scores, the judge used):

Features tried, measured on the -O1-heavy game (needs-O1 against needs-O2; "both" left out):

- load immediately followed by its use, as a share of loads: median 0.57 (-O1) against 0.25 (-O2); at 90% precision,
  5% recall. FP loads (`lwc1`) are left unscheduled at -O2 too, so they say nothing.
- "fixable stalls" (an integer load used by the next instruction while an independent instruction in the same block
  could have moved between them): 4 or more gave 97% precision at 12% recall. GCC 2.7.2's scheduler leaves many such
  stalls at -O2 (lw→sw through two base registers, hi/lo hazards), so lower thresholds fall to 66-81%.
- `lui` immediately followed by its use: 96% against 90% of `lui`s, no use.
- epilogue restores interleaved with body code: no difference.
- **prologue order** (framed functions saving two or more registers): unscheduled GCC 2.7.2 stores `$ra` first and the
  saved registers after it in descending offset order with nothing between; sched2 reorders the saves and pulls body
  code among them. "$ra first and nothing between": 71 of 75 needs-O1 functions, 4 of 168 needs-O2: precision 95%,
  recall 95%.
- **dependency chains** (frameless functions): share of neighbouring straight-line pairs where the second reads what the
  first wrote, at least 16 pairs: >= 0.6 gave 90% precision, 64% recall. Within-game held-out (20 random halves, the
  threshold chosen on one half, measured on the other): 91% precision, 61% recall for the two rules together.

Then the same two rules on other KMC games (needs-O1 / needs-O2 by the same relabelling):

| game (kind) | needs-O1 | needs-O2 | flagged right | flagged wrong | precision | recall |
| --- | --- | --- | --- | --- | --- | --- |
| the -O1-heavy racing game (fitted) | 238 | 300 | 152 | 13 | 92% | 64% |
| an adventure game, 1,800 matched | 17 | 1,416 | 10 | 106 | 9% | 59% |
| a racing game | 6 | 183 | 3 | 4 | 43% | 50% |
| an action game | 10 | 108 | 9 | 15 | 38% | 90% |
| a bowling game (its KMC part) | 19 | 173 | 8 | 8 | 50% | 42% |
| a flight game | 1 | 162 | 0 | 3 | — | 0% |
| a shooter | 8 | 259 | 0 | 0 | — | 0% |
| a tank game | 0 | 363 | 0 | 3 | — | — |

Pooled over the held-out games: 30 right, 139 wrong (18% precision). The prologue rule alone: 28 right, 49 wrong (36%),
recall about 90%. On SN GCC 2.8.1 (an adventure game, 716 needs-O2): 0 right, 20 wrong.

Failed experiments that would otherwise be repeated:

The prologue tell finds -O1 code everywhere (recall ~90% in every game), but 2-19% of each game's -O2 functions also
keep the unscheduled prologue (sched2 had nothing independent to move: the first body instruction depends on a save or
the code is short). Where -O1 is rare, those few percent outnumber the real ones: precision is a property of the game's
-O1 share, not of the rule. It is not a per-function tell, and it was not wired into the profile probe or the brief.
Neither is it for that racing game alone: its open functions are flagged 402 of 494 (81%, against 27% of its matched
code), while a sweep of all 497 under -O1 found none exact and 222 that score worse than under -O2 (177 of those
flagged), so the rule says nothing checkable there either.

If anything comes of it, it would be as a prior: enable it only in a project whose own matched code shows the rule
holding (most flagged matched functions on -O1), which only the fitted game does today; that is unvalidated.

What is controlled evidence and what is one function:

Every label is the judge's (each matched C compiled under the other level and compared byte for byte); the features
read the target words only. No PS1/PS2 GCC project had -O1 ground truth to measure, so nothing is claimed there.
