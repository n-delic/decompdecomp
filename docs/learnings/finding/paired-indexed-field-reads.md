---
title: paired indexed field reads
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A MIPS function reads a byte at offset 10 and a float at offset 12 of the same 16-byte indexed record. Its draft assigned the computed byte address to a pointer local and read both fields through that local. The target computes one address and reads both fields from it.

Spelling the two reads directly as members of the indexed 16-byte record, rather than through the pointer local, reduced the exact judge score from 225 to 210 and removed three register differences. Both candidates contain 534 instructions. The register choice is an observation; why this compiler makes it is a hypothesis.

Giving the temporary a record-pointer type while retaining the local scored 225. Reversing the two source reads also scored 225. Reversing the float multiplication operands scored 230.

These scores are observations on one function, not a controlled compiler regression. The repair has focused synthetic shape and rejection tests; it still needs the judge to assess each child.
