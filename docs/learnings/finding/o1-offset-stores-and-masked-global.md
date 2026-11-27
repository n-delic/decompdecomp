---
title: At -O1, offsets from one array share a base register, and a re-masked narrow global is volatile
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-27
---

A 296-instruction state routine (callee registers saved before the parameter is copied, so `-O1`).

Two source changes, each measured alone on the exact judge, nothing else changed:

- Fourteen stores written as `*(s16 *) ((u8 *) D_X + K)` share one base register (`lui; addiu; sh 0($v0)`). The
  target has `lui $at, %hi(D_X + K); sh ..., %lo(D_X + K)($at)` for each. A separate `extern` per address gives that:
  3705 to 180. decomp-repair `offset_symbol_split`.
- The target loads a `u16` global with `lhu` and masks it again with `andi 0xffff`. A cast or a local copy does not
  give the mask; `extern volatile u16` does: 180 to 0. decomp-repair `remask_volatile`.

Also needed (known already): the four `switch` cases written out with their own bodies, so cross-jumping merges them
(stacked `case` labels compile to a range test; `split_case_labels`).

A second function of the same shape (176 instructions) matched from plain C at `-O1` with `neighbour_globals_record`
alone (505 to 0).

What is controlled and what is one function: the scores are one function each; that gcc's volatile access is what
keeps the mask is a hypothesis, not read in the compiler source.
