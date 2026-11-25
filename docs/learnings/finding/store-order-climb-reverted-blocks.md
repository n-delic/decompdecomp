---
title: store order climb reverted blocks
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags: matched functions of an N64 game (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) that hold a block of eight or more independent stores (an object initialiser, a record writer). 38 of 1,927 matched functions of that game have one. Each had its block put out of order by random single-line moves until the judge scored it between 30 and 105, then the climb of decomp-repair `store_order_climb` (move one line, keep the move when the score does not get worse, a tie included) ran from there with the app's isolated judge.

The source change, and the measured result before and after (scores, the judge used): 100 -> 0 in 192 judge calls, 105 -> 0 in 2, 30 -> 0 in 7, 80 -> 0 in 49 and 90 -> 0 in 297, of five blocks whose revert scored above 0 (two more stayed at 0: the random moves changed nothing the judge sees). Four reached the matched source's own order; the other two reached another order with the same bytes. The app's pass (`app/store_order_climb.py`, in the Deep Permuter and the autopilot's saved-source pass) bounds a run to 300 steps, resumes from the steps it recorded and records a finished climb per saved C.

Failed experiments that would otherwise be repeated: only 6 open functions of that game are within 150, none with a block of eight stores, so no open function was judged.

What is controlled evidence and what is one function: five functions, one random revert each, seeded; not a rate over unseen functions.
