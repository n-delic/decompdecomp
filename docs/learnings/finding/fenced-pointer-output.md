---
title: A pointer output is not a pointer value read
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio handler at score 686 passes the address of an oscillator-state pointer to a callback. A pointer-read fence copied that pointer into a scratch local and passed the scratch local's address instead. A repeated fence also shadowed the first scratch local. The resulting source scored 661 in the isolated ROM judge but was invalid: the callback updates the scratch rather than the state pointer, and the scratch initialization could read an uninitialized pointer.

A score improvement alone is not a semantic verdict. The pointer-read repair now excludes locals whose address escapes, pointer updates, and existing scratch names. The invalid candidate was not submitted. Focused regression tests establish the exclusions; this is a safety finding from one observed near-match, not a controlled compiler match.
