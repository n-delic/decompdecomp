---
title: Split stable pointer call and event uses
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio handler's stable voice pointer feeds a call and a later event-field store. The pointer is initialized once from a field address, never reassigned or address-escaped, and both uses see the same value.

From a 686 candidate in the isolated ROM judge, separate copies for the call and event store scored 350. Applying the existing call-weight ladder to that child scored 260. The `pointer_value_split` repair offers bounded copies only when it can rule out pointer changes, address escapes, hidden macros, C++ references, jumps, ambiguous scopes and compiler introspection. Copies and one-shot scopes are allocation scaffolding, not evidence of the original source.

This is an observed near-match, not a controlled exact match. A compiled callback-and-event harness checks the eligible transformations, while the normal byte judge decides whether a candidate matches.
