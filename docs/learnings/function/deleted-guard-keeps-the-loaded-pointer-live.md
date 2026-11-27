---
title: a guard with identical arms keeps the loaded pointer live and the value takes the next register
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 39-instruction controller-polling leaf: three reads, then a 4-step loop over a flag array, an array of pointers and
an array of copies (`if (flag) copy = *source;`). The target reads `lw $v0,0($a1); lhu $v1,0($v0)`; every candidate
read `lhu $v0,0($v0)` and had the destination, source and counter cursors one register out.

The change, measured with the app's judge (score of the saved near-miss 40):

1. the cursor step both arms of the loop's `if`/`else` ended with, written once after it: 70 (worse alone);
2. `t = **from; if (flag && from && *from) { *to = t; } else { *to = t; }`: 50 alone, 0 together with 1.

The arms are merged and the branch is gone from the object. It was still there at register allocation, so the loaded
pointer was live when the halfword was born, and the two other cursors the guard names ranked above the destination.
Which pass of SN's compiler merges the arms is a hypothesis (no public source). decomp-repair `deleted_guard_keepalive`
offers both edits as one child; its first child from the saved near-miss is exact. The guard is scaffolding.

Failed experiments (all `lhu $v0,0($v0)`): s32/u16 temporaries, a pointer local, struct and bit-field members, inline
helpers, `volatile` on either access, `do { } while (0)` scopes around the load or the store (one reached 60 by moving
other cursors), self-assignments, a guard that only tests a cursor other than the loaded pointer, and an `if (q) {}`
with no arms (removed before allocation). The other SN 2.7.2 builds scored 375 and up; KMC and gcc 2.8.1 140 and up.
All of this is one function.
