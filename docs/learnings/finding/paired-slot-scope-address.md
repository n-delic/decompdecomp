---
title: Pair a scoped slot address with another assignment scope
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

In one MIPS state handler, a byte index selected a 32-bit slot using a local
base assigned inside a single-iteration scope. The isolated linked judge
scored the function 30, with six register differences at that address.

Changing `base + (index * 4)` to `(s32 *)((index * 4) + (u32)base)` and
nesting one more single-iteration scope around the existing base assignment
made the isolated judge exact. The two changes together were necessary:
the address change alone scored 505, and the added scope alone scored 475.
The unsigned index is a byte load, its scaled offset is bounded, and the
32-bit address spelling relies on the specified MIPS compiler and ABI. The
scopes are compiler-matching scaffolding around a real assignment.

The compiler dump allocated the original base and destination pointer to
one register and the index to another; the target uses the opposite temporary
order. This is evidence from one linked function, not a controlled proof of
the compiler's allocation rule. The app's compiler trial separately verifies
the candidate; the per-function profile should only be applied on an exact
trial.
