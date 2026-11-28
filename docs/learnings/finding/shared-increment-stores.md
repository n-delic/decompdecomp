---
title: Inline both arms of an increment store
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A 583-instruction MIPS state handler had two increment paths that copied a
pointer and value into locals, then jumped to a shared `*pointer = value`
store. The compiler allocated the second path's temporary pointer separately
from the shared pointer and emitted an extra move.

Inlining the same store in both increment paths, followed by a jump to the
label immediately after the shared store, left the local assignments intact.
The isolated linked judge improved from 110 to 30: the extra move disappeared,
and six register selections remained. The app's diagnostic compiler trial
recorded 30; its saved O2 source was not changed by that trial. A registered
repair emits the two inlinings together.

Inlining either arm by itself scored 110 or 530, so neither independent edit
explains the improvement. Reordering declarations, reusing index or value
locals, and enclosing increment statements in one-shot scopes did not improve
the 110 candidate. Disabling jump threading or peepholes also tied 110;
enabling CSE across jumps or scheduling made the diagnostic worse.

This is one linked function under the exact profile above, not a controlled
claim about GCC's optimization pass. The explanation that the change affects
register allocation is inferred from the compiler dumps and emitted code.
