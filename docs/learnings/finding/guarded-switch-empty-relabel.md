---
title: guarded switch empty relabel
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2 and -O1
evidence: observation
date: 2026-11-27
---

An N64 function has a guarded eight-entry jump table whose cases 3, 4 and 5 take the implicit default. A draft had an explicit empty case 8; GCC 2.7.2 KMC with `-G0 -mips3 -mgp32 -mfp32 -O2` consequently emitted a nine-entry bound.

Replacing case 8 with empty cases 3, 4 and 5 corrected the bound and improved the app judge score from 48,525 to 48,075. With otherwise identical `-O1` flags, the score improved from 3,860 to 3,410. Any nonempty combination of these three empty labels emitted the same measured instructions in the diagnostic profile. A reusable repair also tried a single in-range empty label and produced both same scores.

Simply removing case 8 made GCC emit a comparison tree and worsened the diagnostic score to 4,670. This is evidence from one function, not a match or a controlled compiler regression test; the repair tests use synthetic source and a guarded table listing.
