---
title: A call's computed argument and its result in one local keep the load out of the argument register
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: observation
date: 2026-11-27
---

What the function looked like: a 34-instruction N64 allocator step `G = (f((H + 7) & ~7) + 7) & ~7; g(a0, a1);
G2 = base - G;` (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1 -g`). The target loads `H` into `$v0` and builds the
argument with `and $a0, $v0, $s0`; ours loaded into `$a0` (20).

The source change: `x = (H + 7) & ~7; x = (f(x) + 7) & ~7; G = x;` with one local was exact (20 -> 0). The local set
twice is a pseudo with two deaths, so global.c allocates it; as one expression local-alloc tied the sum to the `and`
that writes the argument register (explain_regs: a call-argument copy preference).

Failed experiments (all 20): the argument staged in its own local, the result alone through a local, the two in two
locals, a `do { } while (0)` around the load or the call, the callee's prototype as `(s32)`, `(u32)` or `(void *)`, the
global as `u32` or `u8 *`, casts on the call, a literal `-8` mask, and the SN 2.7.2 `-O1` profile.

Evidence: one function, app judge. decomp-repair `call_argument_result_local` (0.6.297).
