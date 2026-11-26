---
title: step across offset store
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

A 92-instruction N64 SDK character converter, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, whose loop writes a
character and sometimes a mark after it.

Writing the one arm as `dst[1] = K; dst++;` instead of `dst++; *dst = K;` gives the loop pass a `dst + 1` giv with its own
register (`move t1,a1; addiu a1,a1,1`, as the target): 1140 -> 0. The saved search best had a second hand-kept cursor
instead and never got below 1225. Parallel byte tables were one `{value, key}` record table, scanned by a `for` with
`break`. decomp-repair `store_across_step` offers both directions.

Controlled: one function; which giv loop.c reduces is a hypothesis about loop.c, not read from its source.
