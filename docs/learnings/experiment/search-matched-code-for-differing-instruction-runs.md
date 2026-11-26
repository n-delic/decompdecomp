---
title: Searching all matched functions for a stuck function's differing instruction runs does not beat its look-alikes
kind: experiment
compiler: gcc-2.7.2-kmc, ido-7.1, sn-n64-281 (each function's own profile)
evidence: observation
date: 2026-11-26
---

Idea: for a near-match, take the target's instructions that differ and search every project's matched functions
(about 47,900 MIPS functions in about 150 projects) for the same short runs, then read the C of the best hits. Built
as a script and measured; **not kept**.

The search: runs of 3 to 6 instructions containing a differing one; opcodes equal, registers renamed by first
appearance inside the run, relocated immediates as %hi/%lo, other immediates ignored except shift amounts and andi
masks; a hit scores, per differing instruction, the rarest shared run over it (runs in over 2% of functions ignored);
same compiler profile preferred; top 3. It was cheap (one uncached scan about 4 s, 66 MB), so cost is not the reason.

Measurement: matched functions that have a stored compiled near-miss (score 5 to 500) were held out. The near-miss was
judged again for its differing rows, the search ran without the function itself, and the proxy was whether a donor's C
holds a line shape (identifiers and numbers blanked) that the matched C has and the near-miss lacks, counting only
shapes found in at most 1% of matched functions. Counted in cases, not lines (a few huge sources swamp line totals).

| donors offered | run 1: 73 cases | run 2: 118 cases |
| --- | --- | --- |
| 3 random functions, same compiler, similar size | 3 to 4 | 25 |
| run search, 3 hits | 27 | 70 |
| 3 look-alikes in the same project (opcode 3-gram overlap) | 26 | 69 |
| 3 look-alikes over all projects | 29 | 66 |
| 6 look-alikes in the same project | 31 | not run |
| same-project look-alikes plus the run search | 34 | 82 |

Run 1 was mostly one SN GCC 2.8.1 game and one KMC GCC game; run 2 added an IDO 7.1 game. The look-alike baseline
was an opcode 3-gram overlap written for the measurement, not the app's `find_similar` itself.

Result: the run search is no better than the look-alikes, and what it adds on top of them (run 1: 26 to 34 cases)
is mostly what three more same-project look-alikes add (26 to 31), at several times the reading (its hits are large
functions: the longer a function, the more runs it can share). Excluding donors that are whole-function look-alikes
(overlap 0.5 or more) dropped it from 27 to 12 of 73 (random: 3 to 4), so more than half of its good hits were
look-alikes found the long way. Capping hit size or requiring equal immediates did not change the picture.

What to do instead: read more look-alikes (`similar.py SLUG FUNC --sources 6`) and the matched neighbours of the
mismatched region (`neighbours.py --source CAND.c`).

Not measured: whether an agent reading a donor goes on to match (the proxy only says the line shape was there to read),
functions stuck for good rather than stored near-misses, PS1 (no little-endian projects were available).
