---
title: parameter reused as return carrier
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -O2
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 78-instruction N64 function (KMC GCC 2.7.2 -O2) returning a flag byte. m2c reused the dead second parameter as the result: arg1 = 0 & 0xFF; ... arg1 = var_s1 & 0xFF; return arg1;.

The source change, and the measured result before and after (scores, the judge used):
The target keeps one flag in s1 and returns var_s1 & 0xFF, with no copy in a1/v0. 305 -> 0 in one step of the new param_result_carrier_fold (drops both writes, returns the flag masked).

Failed experiments that would otherwise be repeated:
Chaining the older families alone stopped at 110 (the extra move v0,a1 remained).

What is controlled evidence and what is one function:
One function; the rewrite is only offered when every change of the flag is followed by a copy statement in the same or an enclosing block (tests/test_param_result_carrier_fold.py).
