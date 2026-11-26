---
title: ABS clamps written as nested if/else arms, and where a shared early return lands
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 114-instruction N64 line-intersection routine of eight `f32` arguments: two arguments clamped to zero near zero, two
special-case arms returning a quotient or 0.5, then the general quotient.

The clamp is `if (((v > 0.0f) ? v : -v) < EPS) v = 0.0f;` (an ABS macro). Written as `if (v > 0) { if (v < EPS) v = 0; }
else { if (-v < EPS) v = 0; }`, jump2 cross-jumps the inner branch with the store; the target keeps one branch per arm
and shares only the store. With a plain rewrite otherwise: 1780 -> 1300 (app's judge). decomp-repair
`nested_branch_condition` writes it, and also reads m2c's `goto` form of the same code.

The remaining rows were the shared `return 0.5f`. The target has it right after the first compare: write it as a label
there (`if (a == c) { half: return 0.5f; }`) and `goto half` from the later tests; the second special-case arm follows
the general code (`if (h != 0.0f) { general } second arm`). Two `run_weight_scope` steps reach the same bytes from the
1300 source without that rewrite.

Failed: separate `return 0.5f` statements (cross-jumping keeps the last copy), `||` conditions (660), nested ternary
returns (620), `fabsf` (another instruction).

One function; the clamp shape is a reproducible compiler behaviour, the return placement is an observation.
