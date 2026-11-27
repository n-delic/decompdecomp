---
title: Shared guarded calls and direct float branches
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

In one large N64 function, a byte equality and range test assigned one of two integer call arguments, with the
special path jumping into a separately guarded call. The target's `beq` and `slti` sequence suggested a sparse
switch. Rewriting the special values as cases and keeping the guard around the default call scored 51,180 -> 50,125
with the exact judge at `-G0 -mips3 -mgp32 -mfp32 -O2`, and 8,240 -> 6,970 in a retained `-O1` trial. The
`shared_guard_call_switch` family offers this shape when it can prove the byte type, predicate, label ownership,
local uses and target comparisons. A controlled test checks all 256 byte values.

Elsewhere in that same function, `if ((x != 1.0f) && (x != -1.0f)) { A } else { B }` produced a materialized
boolean. The target instead has two `c.eq.s` / `bc1t` branches to the same arm. Writing those equality tests as
separate branches to B, retaining each body's scope, scored 50,125 -> 49,265 at -O2 and 6,970 -> 5,970 in an -O1
trial. `float_comparisons_shared_arm` offers the shape only with direct branch evidence and a nonvolatile float
local; its controlled test includes NaN and signed zero.

These are observations from one function, not controlled byte-matching regressions or a match. The function's
recorded profile remained -O2; -O1 results were compiler experiments. Synthetic tests check the source rewrite's
conditions and semantics, not the game's bytes.
