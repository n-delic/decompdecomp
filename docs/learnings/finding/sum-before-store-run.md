---
title: A sum of two loads computed into a local before a run of stores
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function looked like and the exact compiler and flags:
A 25-instruction N64 voice-stop function, 305 before the match. Compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2.

The source change, and the measured result:
`p->a = 0xF; p->b = 0; p->c = x->n + y->m->o;` became `t = x->n + y->m->o;` before the three stores. Family `sum_before_store_run` makes that child (it looks through `do { } while (0)` wrappers). The pre-edit C had debris wrappers and another callee/local name, so the child matches the matched C's structure, checked by eye, not as a byte-exact string. Judge not run on the child.

Failed experiments:
None recorded beyond the limits stated above.

Evidence level: observation.
