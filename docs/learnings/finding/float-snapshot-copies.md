---
title: float snapshot copies
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2 and -O1
evidence: observation
date: 2026-11-27
---

An N64 function built with GCC 2.7.2 KMC and `-G0 -mips3 -mgp32 -mfp32 -O1` had three independent pairs of float snapshots used only for a sum of squares. Applying the existing snapshot-inlining repair at each site separately improved the diagnostic score from 1,335 to 1,165; applying all three together scored 825. Under `-O2` the combined source scored 46,355. The repair now offers the combined child as well as individual children.

In the same function, inlining an intermediate movement expression into the immediately following field store improved the measured scores from 825 to 745 at `-O1` and from 46,355 to 45,975 at `-O2`. Separately, staging a conditional float negation through a distinct local before using it changed the branch layout and improved the diagnostic score from 1,710 to 1,410. These are observations on one function, not a match or a controlled compiler regression test.
