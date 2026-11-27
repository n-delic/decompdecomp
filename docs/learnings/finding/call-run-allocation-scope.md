---
title: Call run allocation scope
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 371-instruction MIPS function had a 355-point difference consisting of register assignments under this exact profile. Its triangle loop ended with four consecutive calls to the same diagnostic function. Wrapping those four calls together in fifteen nested one-shot `do { ... } while (0)` scopes produced the same instructions with the target allocation and scored 0 in the linked app judge. This is allocation scaffolding, not a claim about the original source; it must be disclosed when used.

The compiler dump showed the three global-address pseudos move ahead of the mesh pointer and the outer index move ahead of the triangle index in allocation priority. GCC 2.7.2's `flow.c` counts references using loop depth, while `global.c` orders allocations by a priority based on references and live length. The connection to the observed change is supported by the allocation dumps for this function, not a general guarantee. A declared repair offers the run at several bounded depths, with tests that reject macros and control-flow changes; its generated child was separately scored 0 by the same judge. This is one game function, not an additional independent game match.

One scope around one call improved 355 to 285. Four nested scopes around each of two calls improved it to 215; fourteen around each of three calls reached 85 but still swapped two loop indices. A shared scope at depth 12 also reached 85, and depth 15 matched. Pointer captures, register hints and mesh phase splits did not improve 285. The per-function profile was changed to O1; neighbouring settings were unchanged.
