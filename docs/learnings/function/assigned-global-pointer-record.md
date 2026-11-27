---
title: a global pointer assigned from an allocation, then reloaded before every field store
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 55-instruction voice setup, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. It stores an allocation in
a global pointer and sets 17 fields through it, and the target reloads the pointer (`lw`) before every store. The m2c
draft (`M2C_FIELD(gPointer, T *, K) = ...;` with `extern void *gPointer;`) kept the pointer in a register across several
stores: nine `lw` missing.

The source change, and the measured result before and after (scores, the judge used):

Declare the global as a one-member record, `extern struct { void *block; } gPointer;`, and use `gPointer.block`
everywhere, the assignment of the allocation included. App judge: 1585 -> 0 with no other change. A typed struct for the
object behind a `((struct { T *p; } *) &gPointer)->p` view is exact too.

Failed experiments that would otherwise be repeated:

None. `global_pointer_record` gave no child here before: it refused a global the source assigns and counted only the
`*(T *)((char *)(G) + K) =` store spelling. Both are accepted from decomp-repair PR 2131.

What is controlled evidence and what is one function:

One function. That a member load is invalidated by member stores where a scalar's is not is the same hypothesis as the
family's earlier cases (expr.c's `MEM_IN_STRUCT_P`), not traced through cse.c.
