---
title: negated guard nest vs flat chain
kind: finding
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

An N64 page-stepping routine (41 instructions, one global tested against 1..6, each arm storing it and a second global
and returning) built with sn-n64-281 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. m2c nested the later
arms under `!=` guards with their bodies in reverse order after the nest. gcc lays blocks out in source order, so the
`beq`/`bnel` rows and the arms' order never matched.

The source change: write the flat chain `if (G == 3) { C; return; } if (G == 4) { D; return; } ...`. App judge:
1150 -> 0. Reading the global into a local first also gives 0 on the flat chain and does nothing for the nest (1150).
decomp-repair `negated_guard_unnest` writes the chain from the saved draft (its deepest child is exact; one exchange
alone scores 585).

Failed experiments: a `switch` over the same cases scores 5500 and up (a binary dispatch tree, not the linear chain);
the flat chain with equal pairs staged through one local scores 535; `exit_run_outline` stops at 500.

One function; not a controlled compiler experiment.
