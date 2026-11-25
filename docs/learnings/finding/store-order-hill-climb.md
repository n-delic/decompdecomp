---
title: store order hill climb
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function looked like and the exact compiler and flags: an object initialiser of fifteen independent field
stores built by SN GCC 2.8.1 (`-G0 -mips3 -O2`), a block too long for an all-orders grid (15! orders).

The rule: move one line at a time and keep the move when the score does not get worse (ties kept, no state revisited).
By hand, 118 such steps took one block from 45 to 0, and annealing over the same moves took another from 1085 to 50.

Measured with the fast judge (read-only): the library function `store_order_climb.climb` from a matched block with
one line moved (score 0) restored it; from three lines moved (score 40) it reached 20 in about 400 compiles with a
plateau of 40 or 100 ties, and 30 with 12: the landscape is rough and the climb stalls above 0 here, so it needs the
app's budget and more than one start. Nothing wires it in the app yet (the Deep Permuter local lane is a follow-up).

Failed experiments that would otherwise be repeated: a six-line scramble (score 1248) climbed to 1238 only.

What is controlled evidence and what is one function: one function, one scramble seed.
