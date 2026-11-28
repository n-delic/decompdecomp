---
title: masked stride and loop weight
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

In a 583-instruction N64 state handler, staging the masked input of a loop stride on its own worsened the linked O1 diagnostic from 530 to 700. Adding three nested one-shot scopes around a real post-call indexed reload changed allocation and improved the combined result to 510. The scopes are compiler scaffolding, not evidence of the original source.

The generated child scored 21,513 under the saved `-O2` profile, worse than its saved 20,353; it was not submitted or used to change the profile. This is one function observation, not an exact match or a controlled regression test.
