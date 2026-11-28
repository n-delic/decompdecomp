---
title: Raise a voice allocation call's weight
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio dispatcher's SDK-shaped candidate scored 881 in the isolated ROM judge. A voice pointer occupied a saved register where the target spills it, and several other saved registers differed.

Increasing the one-shot scopes around the voice allocation call from one to four improved the score to 776; seven scopes improved it to 686. The existing `call_weight_ladder` produced 776 from the 881 source and 686 from the 776 source in successive local passes. The scopes are allocation scaffolding, not evidence that the original source had loops. The result is an observed near-match, not a controlled exact match.

Moving the seven note-on local declarations to the enclosing scope in every combination, varying the four initial MIDI assignments and splitting their assignments, or reordering the five independent note-on calculations produced no score below 846 on the separate switch-weight candidate. Weighting the volume, pan and effect-mix assignments in combinations up to twelve scopes each did not improve the 686 candidate.
