---
title: Split a stable pointer before an event store
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio handler scored 686 in the isolated ROM judge. The target spills a voice pointer across calls; the candidate kept it in a saved register. The pointer is initialized once from a field address, never reassigned or address-escaped, and later stored in an event record.

Copying that stable pointer immediately after initialization and storing the copy in the event changed register allocation: an unscoped copy scored 455, and wrapping the copy in one one-shot scope scored 365. The same candidate kept the calls and event value unchanged. The `pointer_value_split` repair offers bounded children in the draft and local lanes, conservatively excluding address escapes, pointer writes, hidden macros, C++ references, jumps and ambiguous scopes. The copy and scope are compiler scaffolding, not evidence of the original source.

This is one observed near-match, not a controlled exact match. A compiled callback-and-event harness tests that eligible transformations preserve the pointer; the project's ordinary byte judge decides whether any candidate matches.
