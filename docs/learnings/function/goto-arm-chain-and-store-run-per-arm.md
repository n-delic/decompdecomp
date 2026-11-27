---
title: goto arm chain and store run per arm
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-27
---

A 92-instruction N64 mode setup (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`) whose
machine draft stood at 910. Two places differed.

1. m2c wrote three arms that store one global as `if (a) { v = K1; goto L; } .. if (z) { v = K3; L: G = v; }`. The
   target has a `lui` per arm and a shared `sw`. Written as `if (a) { G = K1; } else if (b) { G = K2; } else if (z)
   { G = K3; }`: 910 -> 190. decomp-repair `m2c_goto_arm_chain`.
2. `if (n == 0) { v = 4; } else { v = 3; } t[0] = v; .. t[3] = v;` compiled to `v = 3; if (n == 0) v = 4;` (the
   jump pass turning the two sets into one). The target has `bnez; lui; j; li 4; li 3; sw`. Writing the four
   constant stores in each arm gave 0: the stores merge again after allocation. decomp-repair `carried_store_arms`
   already offers it, and reached 0 from the 190 source.

Failed (all 190 or worse): an inline function returning the value, `s16`/`u8` for the local, a ternary, a `switch`,
`goto` and `do { } while (0)` forms of the same `if`, and `var_v1 = K;` kept in each arm before the four stores of
`var_v1`. Several compiled to identical instructions.

Controlled: the two steps measured with the exact judge on this one function. That the jump pass makes the
conversion is a hypothesis from the output; the compiler's source was not read.
