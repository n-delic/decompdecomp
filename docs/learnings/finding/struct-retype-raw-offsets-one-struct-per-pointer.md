---
title: struct retype raw offsets one struct per pointer
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the function looked like and the exact compiler and flags: a 190-line actor update built by SN GCC 2.8.1 (`-G0
-mips3 -O2`) that reads and writes about a dozen fields of three pointers and a global pointer, all through one
padded struct type.

The rule: a member of a struct reached through a pointer is "in struct" to the scheduler's alias test and never
conflicts with a fixed-address global, where the raw `*(T *)((s8 *)p + off)` that m2c writes is unknown and keeps every
load and store where the draft has it. One struct per pointer family (explicit padding so offsets are exact), not a
one-member record per access, is what put the loads and stores at the target's places.

Measured with the fast judge (read-only) on a draft reconstructed by turning the matched struct members into
`M2C_FIELD` accesses: 880 -> 0 for the child that retyped all four pointers together (single pointers: 880, 60, 820,
880). Offered by decomp-repair `struct_retype_raw_offsets`, gated on the function touching a global and storing
through the pointer; function-pointer typedefs count as four-byte members.

Failed experiments that would otherwise be repeated: one pointer at a time did not reach 0 here; the combined child did.

What is controlled evidence and what is one function: one function reconstructed; the family fires broadly (393 of
2,068 saved sources of one game), so most children are expected to be worse and the judge decides.
