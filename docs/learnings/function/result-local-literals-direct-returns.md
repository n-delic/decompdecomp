---
title: result local literals direct returns
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -O1 -g
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 150-instruction N64 function (KMC GCC 2.7.2 -O1 -g) returning 0, 1 or a call's byte: m2c kept the result in var_v0 with repeated var_v0 = 0 and shared return var_v0 tails.

The source change, and the measured result before and after (scores, the judge used):
The target returns each constant where decided: if (flag) { call(); return 1; } return 0;. 695 -> 120 by default_into_else, then 0 by the new result_constant_returns (695 -> 0 in one step from the original draft). The hand-written clean source also split a combined x < 2 || x == 2 into two guards and held the global base in a block-scoped pointer, but neither was needed once the constant returns were direct.

Failed experiments that would otherwise be repeated:
Keeping var_v0 as a carrier left a move per path (120).

What is controlled evidence and what is one function:
One function; the dataflow handles if/else trees only (no loops, switch, goto): tests/test_result_constant_returns.py.
