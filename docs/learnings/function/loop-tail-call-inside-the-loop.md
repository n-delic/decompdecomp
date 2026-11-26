---
title: A table walk's post-loop call written inside the loop on its exit
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 131-instruction walk over a linked table of
boxes (N64), gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`. The target compares two `u16` indexes at the loop end with an
`andi` of each inside the body, with the saved start index not hoisted, and ends with one call that has a single
argument.

The source change, and the measured result before and after (scores, the judge used): from a plain `do {} while`
with the call after it (app judge 600), `for (;;) { body; if (index == start) { index = start; call(text); break; } }`
matched: `do_exit_into_loop` 600 -> 305, `call_arity` (the call had stray arguments) 295, `statement_slide` (the
parameter store above the call, which needed parameters treated as locals) 0.

Failed experiments that would otherwise be repeated: `do {} while` with `goto done` or `return` for the found case
(505 to 1235) all hoisted the start index's `andi` out of the loop.

What is controlled evidence and what is one function: the chain is reproduced by the library's families on one function.
That the call inside the loop halves loop.c's hoisting threshold is a hypothesis (not confirmed in the dumps).
