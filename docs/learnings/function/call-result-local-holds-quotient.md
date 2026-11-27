---
title: call result local holds quotient
kind: function
compiler: SN GCC 2.7.2 -G0 -mips3 -O1
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 127-instruction N64 function (SN GCC 2.7.2 -O1) summing a clamped scaled quotient over a list: m2c declared a call result and a separate local for the value computed from it.

The source change, and the measured result before and after (scores, the judge used):
temp_v0 = f(); if (temp_v0 == 0) v = 0x64; else v = x * 0x4650 / temp_v0; clamp; sum += v. The game's source wrote the quotient back into temp_v0, which gives the two-armed result the call result's register: 25 -> 0 (five register rows) with the new result_local_reuse.

Failed experiments that would otherwise be repeated:
The clamp arms and 0xFF stores were not the cause; only the register of the result mattered.

What is controlled evidence and what is one function:
One function; tests/test_result_local_reuse.py.
