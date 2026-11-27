---
title: one stack literal passed (masked) to two calls is a local set before the first stores
kind: function
compiler: KMC GCC 2.7.2 -O1 -g -G0 -mips3
evidence: verified
date: 2026-11-27
---

What the function looked like and the compiler:

A 124-instruction N64 effect constructor calling an eleven-argument builder twice; m2c wrote the last stack argument 0x40 & 0xFF at both calls. The saved best also carried local_read_fence scaffolding (do/while and __fence blocks) around the first call.

The source change and measured result (app judge):

s32 temp_s2; temp_s2 = 0x40; set after the header stores of the new object (before D_800FFC88 = ...), both calls pass temp_s2 & 0xFF. With the scaffolding stripped: 726 -> 0 (505 -> 0 against the saved best with the fences removed by hand). decomp-repair repeated_stack_literal_local (0.6.295). Distinct from integer_argument_early_local, which takes one bare literal and walks back three statements; here the literal repeats across calls and the set walks back up to a dozen.

Failed experiments:

A literal per call (kept at 726/505); the existing chain from the stripped source found only address_temporary (605). Limitation: a call inside a one-line fence block is not a statement the module reads, so it needs the scaffolding stripped first.

Controlled evidence:

One function.
