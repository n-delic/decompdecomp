---
title: o1 pointer reused for call result and arm head copy
kind: function
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 142-instruction N64 function that spawns an object from a record, with an 18-argument call and a second call in one
`else` arm. Built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (the project's usual `-O2` shape was 955 after many
attempts). The target holds a record pointer and later a call result in one saved register.

The source change, and the measured result before and after (scores, the judge used):
A plain rewrite with typed records scored 526 under -O1. `pointer_integer_phase_union` (the pointer and the later
integer in one storage) gave 210. The last rows were two `andi`s in the wrong order at the head of the `else` arm: a
`s32` copy of the `u16` call result at the arm's head gave 0 (decomp-repair `arm_head_widen`). Judged by the app.

Failed experiments that would otherwise be repeated:
`u16` copy of the value at the arm's head (410); the call result declared `s32` and masked in a second local.

What is controlled evidence and what is one function: one function. prove_chain reaches 0 from the plain rewrite with
the two families.
