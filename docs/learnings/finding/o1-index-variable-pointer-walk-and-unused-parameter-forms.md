---
title: -O1 forms: a constant index in a variable, a walked output pointer, an unused parameter, a loop literal
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

N64 functions of 320 to 450 instructions built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, written as plain
typed C from the assembly. Scores are the app's own judge run standalone; each final source was also recorded as exact
by the app.

The source change, and the measured result before and after:

- Constant index kept in a variable. The first element of a record array is filled with plain absolute stores and
  indexed again after a call. Writing `rec[0].f` makes cse share the address in a saved register; `i = 0;` and then
  `rec[i].f`, with `&rec[i]` as the call argument, gave the target's form (1015 -> 0 on a 351-instruction function).
- Walked output pointer. A loop writing one record per pass through `out[i].f` scored 22335; the same source with
  `out->f` and `i++, out++` was exact (a 371-instruction function).
- Unused parameter. The target loaded the last float parameter from the caller's stack where ours moved `$a3`; an
  unused `f32` before it went 200 -> 0 (372 instructions). decomp-repair `unused_parameter_insert`.
- A literal in a nested loop's condition, set into a local just before the loop: 215 -> 30, then 0 with
  `statement_scope_weight` (323 instructions). decomp-repair `loop_condition_literal_local`.
- A zero-filled "i == 0" read (`array[i] = table[0].byte;` in a loop) and a one-argument call to a two-parameter
  callee (the second register is set by the branch's delay slot) both occur; read the delay slots before adding an
  argument.
- Loop top tests the target does not copy before the loop: leave a `for (;;)` with `return`, and leave an inner loop
  with `goto` to a label after it, not `break` (the existing `loop_top_test_scope` family reaches much of this).

Failed experiments that would otherwise be repeated:

- On a 371-instruction mesh walker, spelling and declaration-order variants left the mesh pointer ranked above three
  hoisted symbol addresses (355, registers only). Declaration order did move the two spill slots.
- On float-orbit code, `m[n].x = sinf(a) * d - ...` needs the first product in its own statement or comma expression;
  the register order of the hoisted float constants stayed open (400, registers only).

What is controlled evidence and what is one function: each item is one function, measured before and after; the two
library families also have unit tests in decomp-repair.
