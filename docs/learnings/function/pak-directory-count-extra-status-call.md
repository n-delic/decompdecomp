---
title: a call the target never makes, kept by a candidate taken from a relative of the function
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-27
---

A 62-instruction controller pak directory count (SDK code). Its saved candidate, adapted from matched code of other
builds, ended with a call to a status routine and returned that call's result. The target has no such `jal`: it stores
the two counts and returns 0. The function also had a per-function SN profile; the project's KMC profile is the one.

Measured with the app's judge, from the saved candidate under KMC:

* saved candidate: 500 (the extra `jal`, and the parameter and the loop counter in each other's saved registers);
* `v = f(...);` written as `v = 0;`: 1120, worse;
* written as `return 0;`: 95, only the `$s0`/`$s1` exchange left;
* then `goto_backward_loop`: 0. `return 0;` alone also removes the exchange in a source written from the SDK's own
  shape (early returns and a `for`), which was exact at once.

decomp-repair `absent_call_drop` offers the call written out from the target's assembly. From the saved candidate the
app's proposal path reaches 0 in two steps (`absent_call_drop`, `goto_backward_loop`).

Failed experiment: under the SN profile the SDK-shaped source scored 1849.

What is controlled evidence and what is one function: one function.
