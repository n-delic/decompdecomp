---
title: A stepped angle read back from its record before the range test
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 93-instruction N64 object update that steps an angle member in a record array (stride 0x64) and wraps it past 360.
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, a `u16` index parameter and the record array declared as one struct.

The target loads the member again after the stepping store, compares and subtracts that register. Plain statements
forward the stored value: 315. The member declared `volatile` alone: 300 (it also reloads before the subtraction). A
local for the compared value alone: 315. Both together: 0. decomp-repair `wrap_reread_member` writes the combination.
Reading the member through a second symbol at the same address (with the local) also gave 0, which is what was saved.

Failed experiments: `volatile` casts on single accesses (580 to 1505), an inline helper through a record pointer
(1915), `do { } while (0)` around the step (970). The saved best built from separate per-field symbols and
scaffolding scored 346; under `-O2` the same plain source scored 4053.

Controlled and one-function evidence: one function.
