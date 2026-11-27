---
title: A pointer global loaded again after member stores, declared as an array read at element 0
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-27
---

A 48-instruction N64 object update copies two halfwords and a byte plus one from the record a pointer global names
into the record its argument names. The target loads the global three times through one `%hi` register
(`lui $v1; lw $v0, %lo(G)($v1)` ... `lw $v0, %lo(G)($v1)`); with `extern void *G;` ours loaded it twice and kept the
second value in `$v1` (score 115, 3 register rows and 1 missing `lw`).

Change: `extern void *G[];` and every read `G[0]`, nothing else. 115 -> 0, byte-identical in the app. The saved best's
statement order already had the byte read before the stores.

Controlled snippet (same compiler and flags): `out[1] = A(G, 0) + A(G, 8); out[3] = A(G, 2);` with `u16` stores.
`extern void *G;` loads `G` once for all three reads; `G[0]` of `extern void *G[];` loads it, shares it between the two
reads and loads it again after the store; `extern void *volatile G;` loads it for each of the three reads. So the array
form reaches targets `volatile` cannot (loads fewer than mentions). Hypothesis: a store marked as a structure member
does not invalidate a plain scalar's load, but does invalidate an element's (not traced through the compiler source).

Not needed or did not work: the second store through integer arithmetic or `((u16 *)p)[1]`, `volatile` on that store,
`(&G)[0]` or `((void **)&G)[0]` without redeclaring (all 115). The one-member record `extern struct { void *block; } G;`
also gave 0, and so did `extern void *volatile G;` (the library's `reloaded_global`, here loads equal mentions).

Library: `int_global_array_read`, family `reloaded_global_array`.
