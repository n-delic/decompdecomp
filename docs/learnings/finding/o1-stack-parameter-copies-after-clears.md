---
title: Stack parameters copied into locals after the first clears, at -O1
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 165-instruction collision walk with three `f32` parameters on the stack, all reassigned in its loop, had its target's
`move $s2,$zero; move $s1,$zero` ahead of the three `lwc1`s from the caller's frame. With the parameters used directly
the loads come first (120, those rows alone). `vx = argX; vy = argY; vz = argZ;` after the declarations that clear the
two counters was exact (0), measured with the app's judge.

No other library repair improved the 120. decomp-repair `stack_params_entry_copy` (`loop_params_entry_copy`) makes the
child, with all assigned stack parameters copied together.

Controlled: the one source change on one function. Which pass orders the loads is not read from the compiler's source.
