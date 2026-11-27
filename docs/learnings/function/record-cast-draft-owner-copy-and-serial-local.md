---
title: record cast draft owner copy and serial local
kind: function
compiler: SN GCC 2.8.1 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

A 78-instruction N64 effect spawner, SN GCC 2.8.1 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`:
it fills its own record with constants and a serial number read from a `u16` global, then allocates up to three child
records. The saved best was the app's typed m2c draft (every field written as `((Rec *) p)->m`), score 705.

The source change, measured with the app's judge (isolated judge worker, then confirmed in the app):

1. The global read once into a `u16` local above the first member store, and the two later uses taking the local:
   705 -> 300. The target's `lhu` sits above that first store. Family: `global_read_local`.
2. In each of two `if (p != NULL)` arms, the copy of the parent's `owner` member moved above the two constant byte
   stores into the child: 300 -> 180 (or 120) -> 60 -> 0, each arm improving on its own. The target loads the parent's
   member before those stores and stores it after them. Family: `statement_slide` / `statement_slide_far`.

Neither family offered anything on the draft: `statement_slide` refused every statement with `->`, and
`global_read_local` did not count a member store as a store through a pointer. decomp-repair 0.6.304 does both, and
`prove_chain.py --families global_read_local,statement_slide,statement_slide_far` then goes from the saved draft to 0
in four steps. A typed rewrite from the assembly with the same two edits was also exact.

Failed experiments that would otherwise be repeated: both copies moved to the head of their arm, above the `temp =`
statement, with the global local left out scored 1875 from 705, and 1470 with it.

What is controlled evidence and what is one function: the scores are controlled; that this is how the game's source
was written is one function. Why the scheduler needs the copy above the stores was not read in the compiler source.
