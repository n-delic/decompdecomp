---
title: Shared coordinate and masked index allocation
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A MIPS rendering function had two paired coordinate reads and one branch that
measured text before indexing an object array. Its remaining four instruction
rows used a different temporary register for the second coordinate address.

Sharing the coordinate base and pointer locals across those reads, then using
the pointer-sized local for the actual masked object index in the measured
branch, moved the pointer to the target register. The fixed linked-byte judge
went from 20 to 0; the running app recorded an exact trial and saved the match
with this function's O1 profile. The integer/pointer casts, one-shot scopes and
existing unused frame padding are matching scaffolding.

Sharing just the coordinate locals tied at 20. Using the shared pointer for the
later string argument also fixed the original register rows but introduced a
new argument setup mismatch (105, or 75 when staging the preceding argument).
A scoped assignment of the masked index regressed to 720.

This is a single real-function observation, not a controlled compiler
regression. The repair's small synthetic tests check source safety and routing;
the linked judge and app provide the measured match evidence.
