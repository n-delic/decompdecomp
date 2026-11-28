---
title: Reorder switch cases with a shared label
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A MIPS MIDI dispatcher had seven non-fallthrough switch cases; one jumped into a label inside another case for the shared note-off path. Moving the pressure case before the controller case, while keeping each braced body and the shared goto intact, changed the compiled body order to match the target more closely. The linked judge improved from 9066 to 6551, and the app saved the new best. The function remained open.

The exact compiler-pass reason for the layout has not been established. A declared repair offers bounded one-case moves only when braced cases terminate and no external jump enters the switch; its generated child independently scored 6551. Synthetic tests cover those guards, not additional game matches. Other trial orders scored 9116 to 12941. The candidate retained the separately documented target-proved stack records and switch reconstruction.
