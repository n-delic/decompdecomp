---
title: Bounded float lifetime and one-shot scope
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A MIPS function used a float derived from a signed byte, then clamped and adjusted by bounded constants. An indexed-record source scored 272 against the linked original with the app judge. In a later conditional zero store, copying that float to an existing dead signed integer local and storing `local & ~local` retained the same zero value and scored 125. The conversion is defined for this bounded input. Wrapping the earlier float conditional in a one-shot `do { ... } while (0)` scope together with the zero expression scored 0; the app verified all 383 instructions as matching. Both constructs are allocation scaffolding and should be disclosed when used.

The compiler's `flow.c` loop weighting and `global.c` register allocation explain why scope and lifetime interact: the zero expression extends the float's lifetime while the one-shot scope changes allocation weights. This is an observation for one function, not a general guarantee. The automatic repair rejects unbounded or possibly uninitialized inputs and changes that could make the local observable; synthetic tests cover those guards. No other game matches are claimed.

A fresh integer local for the conversion scored 133 because it changed the stack frame. Reusing the dead existing local scored 125; the scope was still needed for the exact match. The function's O1 profile was applied only to that function.
